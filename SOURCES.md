# Sources

The retailers dealScout monitors, and — just as important — the ones it cannot.

A source earns a place here by passing three tests, each **measured by fetching the shop**
rather than inferred from its reputation:

1. **Reachable.** A politely-headed GET succeeds. Bot protection (Cloudflare, Akamai,
   DataDome) makes a shop unmonitorable however good its stock is.
2. **Stocks true Elite.** The flagship tier, adult RRP over €200 — Nike *Elite*, adidas
   *Elite*, Puma *Ultimate*, Mizuno *Made in Japan*. A shop carrying only Academy / Club /
   League / Pro takedowns is not a source for this hunt. Proven with a named product.
3. **Readable per-size stock.** Ideally exact ("EU 37⅓ is in stock"); at worst a price
   with no sizes, which the judge caps at 🟡 *verify on click*.

Two notes on tier, both of which have caused wrong conclusions here before:

- **"SG-Pro" and "AG-Pro" are soleplates, not tiers.** `Phantom GX II Elite SG-Pro` is a
  genuine Elite boot. The tier trap is a model like `Vapor XVII Pro FG`.
- **A junior Elite lists at €120–130, not €200+.** The RRP > €200 bar qualifies the
  *shop* (it proves the flagship line is carried); it is not a filter on the boot bought.

Last verified: **2026-08-27**.

**Two other things here go stale, and differently.** The retailer facts above rot slowly —
a shop changes platform or stops shipping to Latvia perhaps once a year. The *boot*
catalogue in `data/football_boots.yaml` rots on a season: it records which generation of
each model line is current, and every brand supersedes its flagship annually. It carries
its own `last_verified` for that reason. When a boot the tool surfaces is labelled
`current generation` and obviously is not, that file is the place to look — not the
parsers, which will still be reading the page correctly.

Per-source yields are watched automatically. `dealscout/yields.py` records how many
products each source returned each run and warns when one falls to half its own recent
median, which is a much earlier signal than waiting for a source to reach zero — and a
more honest one, since a collapse to zero looks identical to a broken reader and once
caused this tool to accuse two healthy retailers of being down.

---

## Tier 1 — monitored

Wired into the hunt and read on every run. The **How stock is read** column says how much
each one can answer without a click: a source that states per-size stock can settle a buy
decision outright, and one that states only a price is capped at 🟡 *verify on click*.

| Source | Country | Platform | How stock is read | Trust | Elite proof (measured) |
|---|---|---|---|---|---|
| **11teamsports.com** | 🇩🇪 DE → LV ~€10 DHL | Shopware 6 | one ld+json `Product` block per size | **4.6/5, 12k+ reviews** | Superfly Elite €277–295; Elite is a `serie=` facet |
| **prodirectsport.ie** | 🇮🇪 IE (CZ warehouse) | Shopify | `/products.json` — one request per collection | 4.0/5, **175k+ reviews** | New Balance Furon V9 Elite FG, RRP €230; Superfly XI Elite €289.99 |
| **komanda.lv** | 🇱🇻 LV — shop at Duntes iela 7, Rīga | Shopify | `/products.json` | official adidas Baltic dealer | adidas Predator Elite FT FG €280; Copa Pure IV Elite €240 |
| **futbola-apavi.lv** | 🇱🇻 LV | OpenCart | rendered size boxes (`read_size_boxes`) | small specialist, unrated | Nike Tiempo Maestro Elite FG €250 (RRP €270) |
| **teamsport.lv** | 🇱🇻 LV — **official Nike distributor since 1998**, free shipping >€50, Rīga pickup | Magento 2 | listing tiles for discovery, then the product page's `jsonConfig` swatch blob for **exact per-size stock**, converted US→EU (`read_magento_swatch`) | authorised channel, zero authenticity risk | Legend 10 Elite SG-Pro €120 (was €330); ZM Vapor 16 Elite KM FG €120 (was €350) |
| **sportland.lv** | 🇱🇻 LV — ~80 Baltic stores, official Nike Baltic distributor, **try-on before buying** | Magento 2 / ScandiPWA | its own **GraphQL API**, discovery via sitemap (`magento.py`) — no sizes, so 🟡 only | Frasers Group backing | Nike Phantom GX 2 Elite €99 (was €330); adidas Predator Elite FG €104 (was €260) |
| **voetbalshop.nl** | 🇳🇱 NL → LV **€24.99** ("Other - Europe") | Magento 2 | rendered listing tiles *with a size swatch* (`parse_product_tiles`) — **exact per-size stock** | large NL specialist | adidas F50 Hyperfast Elite Laceless FG €223.99 (RRP €279.99); Nike ZM Superfly 11 Elite FG €289.99 |
| **futbolemotion.com** | 🇪🇸 ES → LV, cost quoted per order | custom | ld+json on the listing — no sizes, so 🟡 only | 4.0/5, replies to 96% of negative reviews | Superfly 11 Elite AG-Pro €289.99; F50 Elite FG L-Tech €279.99 |

**11teamsports** is the best-trusted retailer in the whole segment — eleven teamsports
GmbH (JCK Holding), 66+ stores across 19 countries, and the highest Trustpilot score here
by a wide margin, with returns praised specifically. For a €250+ boot that might not fit,
this is the safest place to send the owner.

**prodirectsport.ie** is the deepest catalogue and the only source pairing a large sale
range with exact EU sizing. Its rendered pages show *UK* sizes — read the JSON, not the
page. The 4.0 score rests on 175,000 reviews, the largest sample in this survey.

**komanda.lv** is the official adidas dealer for the Baltics and the only source with a
shop the boots can be tried on in. It sells at RRP, so `compare_at_price` is always null:
qualify it on absolute price, not on discount. A harness that demands a "was" price will
wrongly reject exactly the authorised dealers most worth trusting.

**futbola-apavi.lv** publishes no ld+json at all and renders its listing as bare image
anchors, so names come from the URL slug and price from markup. Its size picker is a set
of radio boxes and **only renders a size it can actually sell** — a boot down to its last
pair shows one box carrying `data-qty="1"`.

**voetbalshop.nl** is the widest catalogue outside Pro:Direct and the only *listing* here
that states per-size stock, which normally costs a request per product. Its swatch links
the sizes it can sell and prints the rest as plain text, so "is this option a link" is the
shop's own statement of stock rather than an inference about it. Measured 2026-08-26 on
`/en/football-boots.html`: 48 tiles, 34 with exact stock, and the 14 it declines to answer
for are **exactly** the 14 whose own `data-sizes` counter says every size is available —
where the markup draws no distinction and "all in stock" cannot be told from "no stock
rendered". Paging is ignored, as on teamsport.lv, so breadth comes from category URLs.
Its postage is the catch: **€24.99 to Latvia**, the dearest of any source here, so a find
must be about €25 better than a Baltic one to actually be a better buy.

**teamsport.lv states exact per-size stock — in US sizes, which had to be proven before it
could be read.** Its product pages carry a Magento swatch `jsonConfig` blob whose size
options each hold a `products` array: empty means that size is unavailable, non-empty means
it is buyable. That is complete per-size stock, and until 2026-08-27 the tool threw all of
it away — the generic variant reader rejects the labels because they are 5–12.5, a range
that is impossible in EU (EU starts at 35). So every run spent ~22 sequential product
fetches learning nothing.

The reason those numbers were not simply parsed: **5–12.5 is a valid UK *or* US ladder, and
UK and US differ by roughly a full size** — so reading the wrong one would tell the owner a
boot is in his son's size when it is not, the one mistake this engine must never make. The
system was settled with two independent lines of evidence, not plausibility:

1. **The shop states it.** Beside the swatch the page renders
   `<div class="size-additional-info">US izmēri</div>` — Latvian for "US sizes". (`UK`
   appears three times on the page but never near a size; `EUR` is the currency.)
2. **Nike's own ladder fits it exactly.** Nike's authoritative men's footwear chart
   (the `mens_footwear_us`/`size_eu` rows of `nike.com/size-fit/mens-footwear`) maps
   US 6.5→EU 39, 7→40, 8→41, 9→42.5 … 12→46. teamsport's in-stock sets on three probed
   boots (Vapor 16 Elite `FQ1457-446`, Phantom 6 High Elite `HJ2147-446`, Tiempo Maestro
   Elite `HQ3157-101`) are each a continuous slice of that ladder and nothing else — and
   the smallest rung teamsport lists, US 5, is EU 37.5, **exactly the owner's son's Nike
   size**, which is why its ladders bottom out there.

The conversion table (Nike US→EU, primary-sourced from Nike) lives in
`data/size_conversions.yaml`; `dealscout.sizeconvert` reads it and `variants.read_magento_swatch`
applies it. It is deliberately **not inferred**: a size is converted only when the page
names its system *and* the brand's ladder is recorded, and a US label the ladder cannot
place is dropped rather than mapped to a nearest EU size. teamsport is Nike-only in
practice, so only the Nike ladder is carried. Measured 2026-08-27: all three probed boots
now yield exact EU stock (1, 10 and 10 in-stock sizes respectively). If this reader ever
returns nothing where the swatch is clearly populated, check the `US izmēri` marker and the
Nike ladder's `last_verified` before the parser.

**futbolemotion.com** is the largest Spanish specialist and the best source of colourways
that never reach the Baltics. Its listing publishes ld+json for every product, so price
needs no scraping at all. Per-size stock is **not in the page**: the size table renders
`data-type="availability"` placeholders reading "Loading…" and is filled by a separate API
call, so every find here is capped at 🟡 *verify on click*. Two further traps, both nearly
recorded wrong. Its shipping to Latvia is quoted per order ("according to the order's
volume") and never published as a rate, so it deliberately carries **no `shipping:` figure**
— an invented number would silently reorder the shortlist. And it names no brand anywhere
a parser looks: not in the title ("F50 Elite FG L-Tech Football Boots") and not in the
structured data, while filing every boot under `/…/adidas/…`. Under `brands_only` that
read as an unknown brand and rejected the entire shop, so the brand is now recovered from
the retailer's own URL path. Like komanda.lv it publishes no "was" price — judge it on
absolute price, not on discount.

### Siblings — deliberately not monitored

`viskasfutbolui.lt` 🇱🇹 and `putsad.ee` 🇪🇪 are the *same operator, catalogue and prices*
as futbola-apavi.lv, on the same OpenCart theme; all three were checked on 2026-08-26 and
returned identical products at identical prices. They parse correctly, so they are here as
a fallback if the LV storefront goes away — but monitoring all three would triple the
request load for no extra coverage.

The same applies to **11teamsports' other locales** (`.pl`, `.nl`, `.at`): the same
Shopware platform and the same inventory as the `de-de` store already monitored, so they
would add requests without adding a single boot. Skipped for that reason, not a technical one.

---

## Tier 2 — stocks Elite, monitoring incomplete

Worth the work, not yet done. Listed so effort is spent where it pays. **Currently empty**
— as of 2026-08-26 every shop surveyed has either been wired into Tier 1 or has a recorded
reason below why it cannot be. New candidates land here after a qualifier run.

---

## Blocked — cannot be monitored from CI

| Source | Trust / note | Symptom |
|---|---|---|
| **unisportstore.com** 🇩🇰 | Unisport A/S, ~€155M revenue, top-3 European pure-play, €7 flat to LV. 3.7/5. **Now an R-GOL subsidiary.** | HTTP 405 on every request including the homepage — an IP-range block, not a page fault. Previously the best source of all (per-size stock *and* RRP), so the clean route is an **Awin / TradeDoubler affiliate feed**, which a human has to apply for. Not worth further scraping attempts. |
| **sportsdirect.lv** 🇱🇻 | Frasers Group; good for heavily discounted last-season Elite in person | **Readable in principle, unreachable in practice.** Its category HTML does carry a `var ecommerceData = {…}` block with name, price and brand (a genuine Nike Mercurial Vapor 16 Elite at €171), so the parser is not the problem: it is an IP-level Akamai tarpit that times out from here with both aiohttp and curl regardless of headers or TLS. CI runners are datacentre IPs and will be blocked too. Do not wire it in. |
| **intersport.lv** 🇱🇻 | INTERSPORT BALTIJA, authorised Nike + adidas, Rīga mall stores | **Definitively dead as a source.** The site is a one-line page containing `<iframe src="http://www.intersport.com/">` — no catalogue, no API, nothing to read. Not "not yet probed"; there is nothing there. |
| **sportisimo.com** 🇨🇿 | 220+ stores, authorised — but **2.5/5, returns from abroad are the core complaint** | 403 on every category page |
| kickz.com 🇩🇪 | 3.7/5; subsidiary of 11teamsports, so the parent covers it | 403 (Cloudflare + PerimeterX) |
| keller-sports.com 🇩🇪 | 2.9–3.4/5, refund delays | no catalogue recovered |
| geomix.at 🇦🇹 | 2.1/5 and the site was unstable during research | connection failure |
| isport.ee 🇪🇪 | official adidas partner for Estonia since 2008 — clean, adidas-only | reachable, but no product links recoverable from the category page |



## Excluded by choice

- **zalando.lv** — does not carry the true Elite tier.
- **220.lv**, **kaup24.ee** — marketplace aggregators; also 403 on every category page.
- **decathlon.lv** — confirmed negative: Kipsta own-brand plus League/Club tiers only, no
  Nike at all on the Latvian site.
- **r-gol.com** 🇵🇱 — stocks Elite deeply (Superfly 11 Elite €292–305) and ships to LV for
  €5.99, free over €119, which made it look like the best find of the survey. But its
  Trustpilot is **1.6/5**, with orders cancelled after payment and delayed refunds as the
  dominant complaint. Not somewhere to send a €300 order. Its stock is also published
  per *physical shop* (`stationary_shop_id`), so online availability could not be read
  anyway — the size array on the page is a size *chart* with no availability, and reading
  it as stock would invent stock that isn't there.


---

## Adding a source

Run the qualifier before editing config; it answers all three questions at once and will
not be fooled by a shop that merely *looks* promising:

```
python -m dealscout.qualify www.example.com
```

Then add the listing URL to `watch:` in the hunt. Prefer, in order:

1. a Shopify `/collections/<name>/products.json?limit=250` endpoint,
2. a storefront's own GraphQL API (`catalogs:` in the hunt — see `magento.py`),
3. a page whose ld+json states per-size availability,
4. a rendered size picker (`<select>` or radio boxes),
5. rendered listing tiles (`parse_product_tiles`) — always name, link and price, and on a
   theme whose tiles carry a size swatch, exact per-size stock too (voetbalshop.nl),
6. a price with no sizes — still useful, but always 🟡 *verify on click*.

The tile reader is deliberately theme-agnostic. It finds tiles by class token *or* by the
catalogue data a theme hangs off them, keeps only the outermost match, and then reads each
tile strictly inside its own element. That last part is the whole safety of it: a listing
carries more tiles than products, because related-item carousels reuse identical markup,
and a reader that split the page on a marker string would pair a name with a neighbour's
price. Splitting on `product-item-info` does exactly that on voetbalshop, whose price sits
on the element *above* that div and whose size swatch sits *after* it. A wrong price is
worse than no price, so boundaries come from the parsed tree, never from a regex split.


### When a shop looks like it needs a browser

"It needs JavaScript" is usually a claim about *one* page, not the site. Before reaching
for a headless browser — which is slow, fragile and a new dependency in CI — try these,
in this order. teamsport.lv was written off here as Cloudflare-blocked and turned out to
need none of it:

1. **Read `robots.txt` for `Sitemap:` lines.** Sitemaps are static XML, served to anyone,
   and they list every product URL. sportland.lv gives 18,616 products this way; the
   default `/sitemap.xml` may be another storefront's, so take the URL robots names.
2. **Try the listing page, not the product page.** They fail independently. teamsport
   withholds the price on a *product* page and renders it on the *category* page.
3. **Identify the front end, then ask what it talks to.** A shell is not a dead end, it is
   a client — and the API it calls is usually open, because its own browser has no
   credentials either. sportland.lv loads `Scandiweb/pwa`; ScandiPWA is a React storefront
   *for Magento 2*, so its data comes from Magento's GraphQL at `/graphql`, which is
   public by design. (Magento's REST API at `/rest/V1/...` is not — teamsport returns 401
   — so the failure of one says nothing about the other.)
4. **Check whether an SPA embeds its state** (`__NUXT__`, `__NEXT_DATA__`, an inline JSON
   blob) before assuming it fetches everything.

Only when all four fail is a browser actually warranted. So far none of them has been.

voetbalshop.nl is the second case to prove it, and it failed step 3 outright: it is
Magento, but `/graphql` answers **403** from this network, so the route that unlocked
sportland.lv was simply not on offer. That says nothing about the site — its category
page renders all 48 tiles server-side, complete with a per-size swatch, and needed only a
reader that recognised a second theme's conventions. One dead route is not a dead shop.

**On GraphQL specifically:** introspection is the fastest way to learn a schema you have
no documentation for — `{__type(name:"ProductAttributeFilterInput"){inputFields{name}}}`
revealed that sportland accepts only `category_id`, `category_uid` and `url_key`, and no
free-text search, which is exactly why discovery has to come from the sitemap.

