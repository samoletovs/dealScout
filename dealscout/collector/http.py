"""HTTP I/O for the collector — the impure half: fetching bytes and honouring robots.

Everything here talks to the network. It is deliberately separated from the parsers
(which are pure string-to-``Product`` and need no network stub to test) because it has a
different reason to change — politeness, headers, timeouts, error handling — and a
different testing need. The parsers encode retailer intelligence; this encodes courtesy.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp

logger = logging.getLogger(__name__)

# A bare custom User-Agent with no other headers is silently *tarpitted* by the CDN bot
# protection several European retailers sit behind (Akamai especially): TCP connects,
# TLS completes, the request goes out, and no response ever arrives — so it looks like a
# network fault rather than a refusal. A complete, ordinary browser header set is what
# any HTTP client should send anyway. Politeness is enforced where it actually matters:
# one request per watch page, `scrape.delay_seconds` between them, and robots.txt honoured
# (see `robots_allows`).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Accept-Encoding is deliberately absent: aiohttp sets it to what it can actually decode.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9,lv;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ...and some do the exact opposite, which cost this project its best-known retailer for
# months. sportsdirect.lv — the owner's main shop, the one he actually orders from — was
# recorded as "an IP-level Akamai tarpit; CI runners are datacentre IPs and will be
# blocked too. Do not wire it in." The second half of that was an inference, never a
# measurement. Measured on a GitHub runner, 2026-08-28, one category page, four clients:
#
#   BROWSER_HEADERS (claims to be Chrome) ....... TimeoutError
#   no headers at all (aiohttp's own UA) ........ TimeoutError
#   Accept only, no UA override ................. TimeoutError
#   a single honest, self-identifying UA ........ HTTP 200, 812 KB, 71 products, 0.7 s
#
# Akamai is not blocking the IP. It is fingerprinting: a client whose headers claim to be
# Chrome while its TLS/HTTP2 handshake plainly is not gets tarpitted as a liar, and a
# client that says what it actually is gets served. (Round one bore this out from the
# other side — curl claiming Chrome failed in 0.26 s with a HTTP/2 framing error, while
# plain `curl/8.x` fetched the same page.) So for these hosts, honesty is not merely the
# polite option, it is the only one that works — and it is what a bot should send anyway.
HONEST_USER_AGENT = "dealScout/1.0 (+https://github.com/samoletovs/dealScout)"
HONEST_HEADERS = {"User-Agent": HONEST_USER_AGENT}

#: Hosts that must be sent :data:`HONEST_HEADERS` instead of :data:`BROWSER_HEADERS`.
#: Suffix-matched, so one entry covers `www.` and every other subdomain, and the Frasers
#: locales are listed together because they are one estate behind one CDN configuration.
SELF_IDENTIFYING_HOSTS: tuple[str, ...] = (
    "sportsdirect.lv",
    "sportsdirect.lt",
    "sportsdirect.ee",
    "sportsdirect.com",
)


def headers_for(url: str) -> dict[str, str]:
    """The header set this host is known to answer.

    Defaults to :data:`BROWSER_HEADERS`, which is right for every source measured so far
    bar one. The exception is not a special case so much as the general rule catching up:
    a shop that fingerprints its callers would rather be told the truth.
    """
    host = urlsplit(url).netloc.lower().split(":")[0]
    if any(host == known or host.endswith(f".{known}") for known in SELF_IDENTIFYING_HOSTS):
        return dict(HONEST_HEADERS)
    return dict(BROWSER_HEADERS)


async def fetch(url: str, timeout: float = 20.0, retries: int = 1) -> str | None:
    """GET a page's HTML, or None on error/non-200.

    Does NOT consult robots.txt — that is the caller's job (see :func:`robots_allows`),
    so that fetching robots.txt itself cannot recurse.
    """
    headers = headers_for(url)
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("fetch %s -> HTTP %s", url, resp.status)
                        return None
                    return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt >= retries:
                logger.warning("fetch %s failed: %s", url, exc or type(exc).__name__)
                return None
            # A tarpitting CDN drops the first connection far more often than the second.
            logger.info("fetch %s failed (%s) — retrying", url, type(exc).__name__)
            await asyncio.sleep(2.0 * (attempt + 1))
    return None


_ROBOTS: dict[str, RobotFileParser | None] = {}


async def robots_allows(url: str, agent: str = "*") -> bool:
    """True when the host's robots.txt permits fetching ``url``.

    Cached per host, and **fail-open**: an unreadable robots.txt means "no stated rule",
    not "forbidden" — but it is logged, so a silent assumption never passes unnoticed.
    """
    parts = urlsplit(url)
    if not parts.netloc:
        return True
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _ROBOTS:
        # Resolve ``fetch`` through the package rather than calling the local name, so a
        # test that patches ``dealscout.collector.fetch`` — the suite's standard way of
        # stubbing the network — governs the robots.txt request too, exactly as it did
        # when this and ``fetch`` were globals of one module.
        from . import fetch as _fetch

        text = await _fetch(f"{root}/robots.txt", timeout=10.0, retries=0)
        if text is None:
            logger.info("robots.txt unreadable for %s — proceeding", root)
            _ROBOTS[root] = None
        else:
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            _ROBOTS[root] = parser
    parser = _ROBOTS[root]
    if parser is None:
        return True
    if not parser.can_fetch(agent, url):
        logger.warning("robots.txt disallows %s — skipping", url)
        return False
    return True
