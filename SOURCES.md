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

Last verified: **2026-08-26**.

---

## Tier 1 — monitored, exact per-size stock

Everything needed for a buy decision without a click: price, RRP and which sizes are
actually purchasable.

| Source | Country | Platform | How stock is read | Trust | Elite proof (measured) |
|---|---|---|---|---|---|
| **11teamsports.com** | 🇩🇪 DE → LV ~€10 DHL | Shopware 6 | one ld+json `Product` block per size | **4.6/5, 12k+ reviews** | Superfly Elite €277–295; Elite is a `serie=` facet |
| **prodirectsport.ie** | 🇮🇪 IE (CZ warehouse) | Shopify | `/products.json` — one request per collection | 4.0/5, **175k+ reviews** | New Balance Furon V9 Elite FG, RRP €230; Superfly XI Elite €289.99 |
| **komanda.lv** | 🇱🇻 LV — shop at Duntes iela 7, Rīga | Shopify | `/products.json` | official adidas Baltic dealer | adidas Predator Elite FT FG €280; Copa Pure IV Elite €240 |
| **futbola-apavi.lv** | 🇱🇻 LV | OpenCart | rendered size boxes (`read_size_boxes`) | small specialist, unrated | Nike Tiempo Maestro Elite FG €250 (RRP €270) |

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

### Siblings — deliberately not monitored

`viskasfutbolui.lt` 🇱🇹 and `putsad.ee` 🇪🇪 are the *same operator, catalogue and prices*
as futbola-apavi.lv, on the same OpenCart theme; all three were checked on 2026-08-26 and
returned identical products at identical prices. They parse correctly, so they are here as
a fallback if the LV storefront goes away — but monitoring all three would triple the
request load for no extra coverage.

---

## Tier 2 — stocks Elite, monitoring incomplete

Worth the work, not yet done. Listed so effort is spent where it pays.

| Source | Country | Trust | What works | What blocks it |
|---|---|---|---|---|
| **teamsport.lv** | 🇱🇻 LV — SIA VIVA SPORT, **official Nike distributor for Latvia since 1998**; free shipping ≥€50, Rīga pickup | authorised channel, zero authenticity risk | Listing names true Elite (`VAPOR 17 ELITE FG`, `TIEMPO MAESTRO ELITE SG-PRO`); Superfly Elite €295–369 | Magento 2 behind Cloudflare; category pages need JavaScript, so no product links are recoverable by fetch alone. **The highest-value target on this page** — local, authorised, try-on. |
| **intersport.lv** | 🇱🇻 LV — INTERSPORT BALTIJA, authorised Nike + adidas, Rīga mall stores | global franchise | Carries Elite per local stock | Not yet probed; DNS/SSL failed on first attempt. Worth a proper qualifier run. |
| **futbolemotion.com** | 🇪🇸 ES — 18–20 stores, Spain's #1 football specialist | 4.0/5, responds to 96% of negative reviews | Reachable; 46 ld+json products on the listing | Listing publishes no sizes and no Elite proof was recovered from it; needs product-page reading. Good for colourways that never reach the Baltics. |
| **voetbalshop.nl** | 🇳🇱 NL | — | Listing names adidas F50 Hyperfast Elite | No product links recovered without a browser; LV shipping unconfirmed. |

---

## Blocked — cannot be monitored from CI

| Source | Trust / note | Symptom |
|---|---|---|
| **unisportstore.com** 🇩🇰 | Unisport A/S, ~€155M revenue, top-3 European pure-play, €7 flat to LV. 3.7/5. **Now an R-GOL subsidiary.** | HTTP 405 on every request including the homepage. Previously the best source of all — per-size stock *and* RRP — so worth re-testing from another network before writing it off. |
| **sportland.lv** 🇱🇻 | Baltic's largest chain, ~80 stores, official Nike Baltic distributor, 60% Frasers Group. Superfly 11 Elite €295.99. **Best option for trying boots on in Latvia.** | JavaScript SPA; server HTML is an empty shell. Deserves a browser-based reader. |
| **sportisimo.com** 🇨🇿 | 220+ stores, authorised — but **2.5/5, returns from abroad are the core complaint** | 403 on every category page |
| kickz.com 🇩🇪 | 3.7/5; subsidiary of 11teamsports, so the parent covers it | 403 (Cloudflare + PerimeterX) |
| keller-sports.com 🇩🇪 | 2.9–3.4/5, refund delays | no catalogue recovered |
| geomix.at 🇦🇹 | 2.1/5 and the site was unstable during research | connection failure |
| sportsdirect.lv 🇱🇻 | Frasers Group; good for heavily discounted last-season Elite in person | Akamai tarpit: answers a complete browser header set, silently hangs on anything less. Times out on every request in CI. |
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
2. a page whose ld+json states per-size availability,
3. a rendered size picker (`<select>` or radio boxes),
4. a price with no sizes — still useful, but always 🟡 *verify on click*.
