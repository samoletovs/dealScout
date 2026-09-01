# bRoom — vision

> **bRoom** · `broom.naurolabs.com`
> The name hides *Boot Room* — Liverpool's legendary back room where Shankly and
> Paisley built a dynasty out of tea, notes and argument — and keeps a *vroom* kick
> for speed. Abstract enough to own in search, warm enough to carry heritage.

**Status:** v3 — direction changed 2026-09-01. The Archive is the product; the Scout is the second feature.
**Date:** 2026-08-28, amended 2026-09-01

---

## 1. The insight

Football boots are a market with an unusual information asymmetry, and it is
almost entirely created by naming.

The same *name* covers boots that differ by **4× in price** and completely in
construction:

| Title on a retailer's page | What it actually is | Price |
|---|---|---|
| `Predator Elite FG` | adult flagship, Primeknit, carbon-infused plate | **€280** |
| `Predator Elite Juniors FG` | junior flagship — different upper, softer plate, wider last | **€61** |
| `Predator Accuracy Injection+ Childrens Elite FG` | 2023 kids' takedown | **€60** |
| `Unisex Kids CopaP3Elt SG` | Copa Pure 3 Elite, junior, truncated by SportsDirect | **€70** |

Every one of those says "Elite". A parent cannot tell them apart. A fourteen-year-old
who cares enormously often cannot either. Retailers have no incentive to clarify —
the ambiguity sells boots.

And **every price comparison engine on the market matches on title strings.** So they
will cheerfully tell you a €70 junior boot is "the same boot, 75% off" against a €280
adult RRP. The discount is fiction. The comparison is a category error.

> **The gap:** nobody has a *structured, queryable* model of what a football boot
> actually is — brand → silo → generation → tier → audience → soleplate. Everyone has
> articles. Nobody has a database. So nobody can do price comparison honestly.

---

## 2. What we build

Two halves of one thing — but **no longer equal halves**. See the 2026-09-01 direction
change below.

> ### ⚠️ Scope, cut deliberately — 2026-08-28
>
> **Top Elite-tier boots only. Nike and adidas only.** Roughly seven current silos —
> Mercurial Superfly, Mercurial Vapor, Phantom, Tiempo/Maestro, Predator, F50,
> Copa Pure — plus recent generations still on clearance shelves.
>
> This is the most important decision in the document, because it defuses the risk
> the landscape research named as *"what sinks these projects"*: a full catalogue of
> every boot ever made rots every season and cannot be maintained. Tens of boots can
> be kept correct. Thousands cannot.
>
> It also suits the chosen design: a finite, curated set is a **collection**, which
> is exactly what a sticker album is.
>
> Older boots remain as *narrative heritage*, not as dataset rows we must keep true.

> ### 🔁 Direction change — 2026-09-01 (supersedes the scope box above)
>
> The owner reviewed the live site and named the problem plainly: *"still missing a
> lot of information — prices not enough, pictures not enough, details about the boot
> and the history not enough."* Two things change, and the second reverses a decision.
>
> **1. The Archive is the product. The Scout is the second feature.**
> The site is an *educational history of the elite football boot*, written for a
> fourteen-year-old and his teammates. Price is still there and still honest, but it
> is no longer the reason to visit. Nobody returns weekly to a price table; people do
> return to a thing they are learning. The Scout keeps every honesty guarantee it has
> — it simply stops being the headline.
>
> **2. Lineage becomes structured data. This reverses "narrative heritage only".**
> The August scope deliberately kept older boots out of the dataset because a full
> catalogue rots. That reasoning was right about *buyable* boots and wrong about
> *historical* ones, and the difference is the whole point:
>
> > **A discontinued boot cannot rot.** The 1994 Predator's release year, its innovation
> > and who wore it are settled facts. They were true last season and will be true next
> > season. What rots is price, stock and "is this generation current" — and those live
> > on the *buyable* rows, which stay scoped tight.
>
> So history is not a maintenance burden. It is the one part of this dataset that is
> **finished once it is right**, which makes it the best possible thing to invest in.
>
> **The new unit of content is the generation, not the boot.** Predator 1994 →
> Accelerator → Mania → Pulse → Absolute → … → 26, each with the year, what actually
> changed, who wore it, and **a photograph of that generation** so a reader can see
> the lineage evolve down the page. A timeline without a picture per step is a list of
> names; with pictures it is the reason the site exists.
>
> **The buyable set narrows at the same time: adult flagship, RRP ≥ €200.**
> No junior flagship, no academy, no league, no takedowns. The owner's son wears
> 37–37.5, which is already an adult size, so the junior ladder is not merely
> out of scope — it is not the right boot for him. Those tiers stay in the *catalogue*
> (the engine still needs them to classify a title correctly and to refuse a false
> "75% off"), but they leave the site's buyable surface.
>
> ⚠️ **The gate applies to the Scout, never to the Archive — measured 2026-09-01.**
> Applied literally to history it deletes the history: the 1979 Copa Mundial (€140,
> the best-selling football boot ever made) fails it outright, and the 1994 Predator,
> the 1998 R9 Mercurial and the 2002 Mania have *no euro RRP at all* because the euro
> did not exist when they launched. A €200 floor is meaningful for a boot you can buy
> this week and meaningless for one discontinued in 1996.
>
> So the rule splits, exactly like `tier` vs `identity` did:
>
> - **Archive scope** = *was this the flagship of its own era?* Price-blind, era-relative.
> - **Scout scope** = *adult flagship, `launch_rrp_eur` ≥ €200, still sold.* This is
>   what we monitor, alert on, and quote a price for.
>
> The catalogue already carries `launch_rrp_eur` for all 29 known generations, so the
> Scout gate is enforceable today — it needs no new field, which corrects an earlier
> reading of mine that looked for `rrp_eur` on the wrong entity. Under the gate exactly
> one current row drops out, and 28 remain.
>
> The catalogue also warns that `rrp_bands` are *"corroboration only, never a gate —
> price must not decide tier"*. That still holds and is not in tension: this gate
> decides **coverage**, not tier. A discounted flagship is still a flagship; it is
> simply still in scope.

### 🏛 The Archive — *know exactly what you are looking at*

Every Nike and adidas boot, every silo, every generation, every tier, since 1979.
Free, beautiful, multilingual. The page you send someone when they ask "is this a
good boot?".

- **Silo timelines** — Mercurial Vapor 1998 → 17, Predator 1994 → 26, scroll through
  three decades of a single lineage.
- **The tier ladder, taught visually** — the site's core teaching moment. Adult
  flagship vs junior flagship vs takedown, shown side by side, so the difference
  becomes obvious and permanent.
- **Technology, in plain language** — what Flyknit, Vaporposite, Primeknit,
  Demonskin, Sprintframe actually change about a boot on your foot.
- **Brand heritage as story** — Adi Dassler and the 1954 Miracle of Bern. Craig
  Johnston's rejected rubber fins becoming the 1994 Predator. Ronaldo's silver
  Mercurial in 1998. This is the emotional content, and it is why people stay.

### 🔭 The Scout — *the best price for **this** boot, honestly*

For any boot in the Archive, the live best price across retailers — matched on
*identity*, not on title text.

- **Boot-aware matching.** A junior Elite is never compared against an adult Elite RRP.
- **Honest price history.** "Cheapest in 45 days" — a claim that can be checked —
  instead of the retailer's own "was €280", which is its own marketing.
- **Size-aware.** Boot sizing differs by brand; a UK 5.5 Nike is not a UK 5.5 adidas.
- **Deal alerts.** Watch a specific boot in a specific size, get told when it drops.

---

### 🧬 The lineage record — the new core entity (2026-09-01)

A **generation** is not a buyable boot and must not be stored as one. Forcing a 1994
Predator into `boots.yaml` would give it a soleplate list, a retailer handle and a
price identity it never had, and would corrupt the very keys the Scout depends on.

So lineage lives in its own file, `data/broom/lineage/<silo>.yaml`, one file per silo —
which also means seven researchers can work at once without touching each other's data.

| Field | Meaning | Rule |
|---|---|---|
| `silo`, `brand` | which lineage | must match an existing silo |
| `sequence` | position in the lineage, 1 = first | unique per silo, this is the sort order |
| `name` | what it was called | as the brand called it, not a retailer |
| `year` | release year | sourced |
| `innovation` | *what actually changed*, plain language | the heart of the record |
| `why` | the problem it was trying to solve | optional, but this is the story |
| `players` | who wore it, notably | sourced |
| `photo` | a licensed image **of that generation** | `UNVERIFIED` until licence-checked |
| `sources` | URLs backing the above | at least one, and it must be about *this* generation |
| `still_sold` | is it buyable today | links to `boots.yaml` when true |

**Two rules carry over from what the guards already caught, because they will bite
hardest here:**

1. **A source about a different generation is not a source.** The existing
   `test_a_source_url_must_not_cite_a_different_generation` exists because eight rows
   cited a review of a neighbouring boot. A thirty-year lineage is where that mistake
   is easiest to make and hardest to see.
2. **A photo must be of the generation it sits next to.** A Predator Mania photographed
   next to the Accelerator entry teaches the reader something false. `UNVERIFIED` is
   always better than approximately-right, and an honest gap in a timeline is
   interesting in itself.

---

## 3. Why this is credible, and not a from-scratch fantasy


**Most of the hard part already exists in `dealScout`.**

| Already built | File |
|---|---|
| Tier catalogue that knows junior ≠ adult ≠ takedown | `data/football_boots.yaml` |
| Classifier: title → brand, line, generation, tier | `dealscout/catalogue.py` |
| Retailer title repair (SportsDirect truncation, `SG-Pro` traps) | `catalogue.py` `title_rewrites` |
| adidas retired `.1/.2/.3/.4` ladder | `catalogue.py` `legacy_numbered` |
| Honest price memory — "cheapest seen in 45 days" | `dealscout/pricehistory.py` |
| Deal judge | `dealscout/judge.py` |
| Size conversion | `data/size_conversions.yaml` |
| Retailer collectors (ld+json, Shopify, Magento) | `dealscout/collector/` |
| Golden-set regression scoring | `dealscout/eval.py` |

The website is **the front end of an engine that already works.** We are publishing
something we already have, not inventing it.

And `golazo` already proves the delivery stack in this lab: React 19 + Vite +
Tailwind + react-i18next + Framer Motion on Azure Static Web Apps.

---

## 4. Who it is for

1. **The kid.** 12–18, obsessed, knows the boots, has no money, wants the flagship.
   Comes for the Archive — the timelines, the heritage, the "which Mercurial is the
   best ever" argument. Stays for the deal alerts.
2. **The parent.** Buying for the kid, terrified of paying €280, cannot tell a
   junior Elite from an adult one. Comes for the Scout. Is quietly educated by the
   Archive.
3. **The Baltic / Russian-speaking player.** Genuinely underserved. There is
   effectively **no Latvian-language football boot content at all**, and Russian
   coverage is thin. This is the wedge.

---

## 5. Languages

**English, Latvian, Russian** — as first-class equals, not English with translations
bolted on.

- Boot **model names stay in English always** (`Mercurial Superfly 11 Elite`) — that
  is how they are sold everywhere. Never translate a product name.
- Everything else — tier explanations, technology, heritage, guidance — fully
  localised, written not machine-dumped.
- Latvian and Russian run ~15–30% longer than English; the layout must be designed
  for the longest language, not retrofitted.

---

## 6. What "amazing design" has to mean here

Not decoration. The design **is** the argument.

### Chosen direction: 🃏 Sticker Book

Panini-album culture — collectable cards, foil edges, matchday print energy. Picked
over "Chalkboard" (tactics board) and "Instrument" (technical/data-forward) after all
three were built and screenshotted at desktop and 390px in English and Russian.

Why it wins beyond taste: a **finite Elite-only set is a collection**, and a sticker
album is the native visual language for a collection. The scope cut and the design
direction reinforce each other rather than fighting.

Verified before selection: `Rubik Mono One` renders Cyrillic in the display headline,
stickers stack cleanly at 390px with foil edges intact, and the stamps translate
(ЕСТЬ / ОБМЕН?). Every typeface used was checked for real `cyrillic` + `latin-ext`
subset coverage by fetching the Google Fonts CSS — not assumed.

**Rejected:** near-black with an acid-lime accent (`mockup-01`), which is a known
AI-default look; and a cream-paper direction at `#F3EFE6`, which is three hex points
from the *other* named AI default.

Two live refinements: **real boot photography must be the hero of each sticker**, and
the maximalism needs dialling back — the client's note on comparable sites was
*"too much noise"*.

### The signature move

The **sticker**. A boot as a collectable card, with its tier, generation and honest
price on it. Inherently screenshot-able and shareable, which is how a site with no
marketing budget gets found.

The most important *screen* remains the **tier explainer** — the moment a visitor
sees that a €280 "Predator Elite" and a €61 "Predator Elite Juniors" are not the same
boot. If that lands, the site has earned trust for everything else.

### 🔴 The open problem: photography

The client requires **real boot pictures**. We own none, and product photography is
copyrighted by the brand, retailer or photographer — hotlinking press shots creates
genuine exposure. Under investigation now: affiliate feeds (which typically licence
imagery to approved affiliates), brand press resources, openly-licensed sources, and
commissioned illustration as a durable fallback.

**Promising lead:** `prodirectsport.ie` exposes `compare_at_price`, which is a
*Shopify* convention — so it likely serves structured product JSON with image URLs,
sizes, prices and stock together. If one integration supplies all four, it reshapes
the entire build.

Access and licence are separate questions, and both must be answered before any
image ships.

---

### 🖼 Photography: resolved as a hybrid (2026-08-28)

The client requires **real boot pictures**. Investigation established that retrieval
and licence are separate problems, and only one of them is hard.

**Retrieval is already solved.** The Shopify `/products.json` we fetch for prices also
carries full-resolution image URLs — we simply discard them. Verified live: Pro:Direct
returns five images per product at 1065×1065 from `cdn.shopify.com`.

> ### ⚠️ Incident, 2026-08-28 — recorded because the lesson is the point
>
> Four of those retrieved images — real adidas product photography — were committed
> to the public `bRoom` repository during scaffolding. Retrieval had been approved for
> an internal design spike; **committing to a public repo turned retrieval into
> publication**, which was not approved and is not licensed.
>
> Removed, gitignored repo-wide, history rewritten, repo set private. A finding worth
> keeping: **a force-push does not remove data from GitHub** — the orphaned blob was
> still fetchable by SHA afterwards (`200`, 1.1 MB), and only going private returned
> a `404`. Final purge needs `delete_repo` scope: `bRoom` issue #1.
>
> The rule now lives in `design/assets/README.md` next to the code, not only in a
> vision document nobody reads at commit time.

**The licence to display is the hard half:**

| Source | Covers | Verdict |
|---|---|---|
| **Own artwork** | All 7 silos | ✅ **Baseline.** We own it outright — immune to affiliate rejection, CDN rot, delisting |
| **adidas via Awin feed** | Predator, F50, Copa Pure | 🟡 Awin feeds carry a licensed `image_url`, but only to *accepted* affiliates. **Not yet applied for.** |
| **Wikimedia Commons** | Older generations only | 🟡 Heritage gap-fill; current Elite silos effectively uncovered |
| **Nike's own affiliate programme** | — | ❌ Restricts price-comparison affiliates |
| **Brand newsrooms** | — | ❌ Editorial use only |

**Open follow-up:** Nike photography may still be reachable through a **retailer's**
affiliate feed rather than Nike's own — Pro:Direct shoot their product photography
in-house, so they may hold the copyright and be able to license it onward. Copyright
in the photograph and trademark in the product shown are different rights.

> ### The design rule this produces
>
> **Comparison surfaces use consistent artwork we own. Single-product surfaces may
> use licensed photography.**
>
> #### The evidence — an accidental experiment, 2026-08-28
>
> Two sessions were given contradicting instructions and the tier row briefly rendered:
>
> | Card | Rendering | Price |
> |---|---|---|
> | Adult flagship | 🎨 illustrated | €280 |
> | Junior flagship | 📷 **real photograph** | €61 |
> | Kids' takedown | 🎨 illustrated | €60 |
>
> The €61 boot looked dramatically more desirable than the €280 one. **The comparison
> did not merely weaken — it inverted**, teaching the exact opposite of its purpose.
>
> The principle had been argued in the abstract beforehand; the screenshot proved it.
> A coordination failure that earned its cost.
>
> #### Why artwork wins on the tier explainer, beyond licensing
>
> 1. **Fairness** — artwork is guaranteed available for all three cards, so they can
>    never drift apart. Photography cannot promise that while the Nike licence is open.
> 2. **Honesty** — product photographs are marketing images, each shot to flatter its
>    own boot. Comparing three of them does not isolate the variable the screen exists
>    to demonstrate.
> 3. **It teaches** — a drawing can encode the *actual* differences: the junior's wider
>    last, its softer plate, the takedown's injected rather than carbon plate. A
>    photograph shows a colourway; a diagram can show the claim.
>
> This removes photography from the launch critical path entirely. An affiliate
> rejection now costs us nothing on the most important screen.

### 📐 The image contract

Constraints discovered by building against real assets rather than assumed ones:

1. **Real retailer shots are square, white-background, ~1065×1065** — *not* transparent
   cut-outs. Designing against clean alpha PNGs would have validated a fiction.
2. **A stored image URL is only meaningful with a timestamp.** Merchant CDNs rotate
   them (Shopify appends a `?v=` cache-buster), so `image_seen_at` is stored alongside
   — and set only when an image exists, so an empty stamp never reads as "seen now".
3. **Every card must survive with no image at all.** An `onerror` hook renders a
   deliberate "no photo on file yet" state; some boots will always lack one.
4. **A pair being compared must be a matched set.** Photos arriving from different
   retailer feeds differ in scale, crop, lean and lighting — and that inconsistency
   reads as *"these are different kinds of listing"*, reintroducing the exact confusion
   the site exists to remove. A layout can normalise the frame but not how the boot
   sits inside the source photo. So prefer **one retailer's shot of both** boots in a
   pair over mixing sources.

### 🎨 What the artwork may claim

`data/broom/tiers.yaml` carries an explicit `draw:` / `do_not_draw:` contract, because
**a physical claim becomes a shape on the page** — write "wider last" and a wider boot
gets drawn.

| | Adult flagship | Junior flagship | Takedown |
|---|---|---|---|
| **May draw** | most rigid plate (carbon/Pebax); lowest, thinnest upper | visibly less rigid plate (TPU, not carbon); slightly fuller profile | injected plate |
| **Must not draw** | last width, exact weight | last width — *"a MODEST difference, never a wide-boot silhouette"* | — |

No measured Nike or adidas last-width figure exists for **any** tier. The copy says
"wider last"; the honest picture is subtle. Weight ranges are silo-wide, not per-boot.

This is where artwork beats photography: **a photograph cannot show a plate.** Plate
rigidity and upper profile are the two honest levers, and they are exactly what the
tier explainer needs to teach.

### 🔑 Pro:Direct — one integration, both brands (and why Nike still fails)

The Nike question turned on two rights that are easy to conflate:

- **Copyright in the photograph** — the *retailer's*, if they shot it. Pro:Direct and
  Unisport photograph in-house, so they own it.
- **Trademark in the product depicted** — Nike's, but depicting a genuine boot you
  link buyers to is nominative fair use.

So Nike's own programme being closed to comparison sites does **not** by itself close
the door. **Pro:Direct is on Awin, sells both Nike and adidas, and shoots its own
photography** — one integration could in principle deliver images, price, size, stock
and an affiliate link for the whole catalogue.

> #### The finding that settles it, and it is upstream of all that
>
> **Nike's authorised-retailer agreements typically forbid syndicating Nike product
> imagery to third-party sites and to affiliate networks Nike has not partnered with.**
>
> So Pro:Direct may own the copyright and still be contractually barred from licensing
> it onward to us. **The right to hold a copyright and the right to grant it are
> different things** — and the blocker was never the retailer's rights, but the
> retailer's own contract.
>
> Corroborated by multiple secondary sources and consistent with Nike gating assets
> behind Brandfolder. **Labelled a strong, cited "probably blocked" — not a proven
> no.** The operative clause lives in a signed agreement we cannot read.
>
> One instrument would settle it: after Awin acceptance, read Pro:Direct's
> Terms/Branding tab, or ask the affiliate manager in writing. That converts a
> permanent maybe into a scheduled question.

**Posture:** Nike is planned as **illustrated**, with a *cited* reason we can defend —
including to ourselves in six months, when someone reasonably proposes "why not just
use the retailer's photos?". Apply to Pro:Direct anyway: the adidas half justifies it
alone, and it is the only cheap experiment that could still surprise us.

### 🔊 One integration, four data types

The single loudest finding. The same feed that supplies images also supplies **price,
`compare_at_price` (a real RRP), per-size stock, and — via Awin — a monetised deep
buy link.**

For adidas, *picture + price + sizes + stock + affiliate revenue is one integration.*

Imagery work and price-finder work are therefore **the same pipeline per brand**, not
two competing workstreams. This reshapes the build: we integrate per retailer, and
each integration lights up both halves of the site at once.

---

### 🧪 What the guards actually caught

The `draw:` contract is not theoretical — it caught four wrong claims before any reached a
reader, across **three distinct failure modes**. Recorded because the modes recur:

| # | The claim | Why it was wrong | Mode |
|---|---|---|---|
| 1 | `"LACED (never laceless)"` on junior, `measured`, *"draw laces on the junior card"* | A laceless junior Elite exists — €63.00, sportsdirect.lv | **Absolute** |
| 2 | `"adidas League is never laceless"`, hidden in a *note* | adidas sold Predator 20.3 Laceless at what is now League | **Absolute, in a field the guard didn't read** |
| 3 | `"laced only"` on the Copa junior in `boots.yaml` | Same error, different file | **Absolute** |
| 4 | `"LACELESS on Predator Elite"`, `measured` | adidas sells `Predator Elite FG` *and* `Predator Elite Laceless FG` at the same tier | **SKU variant asserted as a tier property** |

**Two lessons cost a round each:**

1. **A guard reading only the `value` field misses the `note`.** The note is what a person
   reads before drawing, so it makes the same promise. Widening the check immediately found
   claim 2.
2. **Downgrading a claim to `directional` is not enough — it must leave `draw.show`.**
   Anything in that block is read as a tier difference *regardless of confidence*. The tag
   governs how hard to draw something, not whether to. A caveated wrong claim still teaches
   the wrong thing.

**"Never" is the most expensive word in a dataset.** A source describing what a tier
*usually* looks like becomes what it *always* looks like, `measured` presents it to an
illustrator as fact, and one counter-example destroys it — by which time it is a picture the
reader cannot audit.

The guards now target the **category** (`closure` may never be a tier drawable) as well as the
**phrasing** (no absolutes on a `measured` claim). Both were needed: claim 4 contained no
absolute, and claims 1–3 were not category errors.

### 🔗 The join-key manifest

`data/broom/join-keys.json` publishes every `(brand, line, generation)` the classifier can
answer, with the `year` / `status` / `launch_rrp_eur` each resolves to — so bRoom can verify
its references **across repositories**, which it previously could not do at all.

It is the agreed migration trigger, now met: the presentation dataset may move into bRoom,
prose first, `boots.yaml` and its test last.

Three guards, because a manifest another repo trusts fails invisibly: **staleness** (in CI via
`--check`), **agreement** with the in-repo join guard, and **completeness** of the fields
bRoom must not restate.

> A portability bug worth remembering: the digest originally hashed **raw bytes**, so the same
> file on Windows (CRLF) and Linux (LF) hashed differently and the staleness guard fired on a
> change that never happened. Hash the *text*. CI caught what a Windows machine never could.

---

## 7. Honest risks

### The landscape verdict, recorded in full

Research was explicitly instructed not to flatter the idea. It didn't:

- **`studsbase.com` is building a structured boot database right now** — silo and
  soleplate filters, compare view. Incomplete (Elite-only, placeholder images,
  "Coming Soon"), but "nobody has a real database, only articles" is **no longer true**.
- **`FOOTY.COM` and `bootstracker` already do boot-aware price comparison.**
- **Footy Headlines** runs a Boots DB with a six-language switcher — **but no LV or RU**.
- **Nike restricts deal/comparison affiliates**, capping revenue on half our content.
- Realistic scale: **low thousands of visitors per month**, not hundreds of thousands.

> **Its verdict: narrow it.** The "beautiful global encyclopedia + deal finder"
> competes with Footy Headlines, Studs, FOOTY.COM and Unisport *simultaneously* and
> beats none of them.

**The gap it found is real but specific** — the intersection of three things nobody
combines:

1. Tier and junior disambiguation
2. Welded to verifiable, boot-aware price history
3. In EN + LV + RU — **no player is trilingual**

**Decision (2026-08-28): proceed, with the Elite-only scope cut.** Taken with the
verdict in hand, not in ignorance of it. The reasoning: the narrow product is close
to what `dealScout` already does well, so we publish an existing strength rather than
maintain an encyclopedia; and the client's own read of the incumbents was that they
are *"too much noise"* — which is a design and clarity gap, not a data gap, and is
precisely what the Sticker Book direction and the tier explainer attack.

This is a **craft project with a real niche**, not a business. Recorded as such.

### Risk table

| Risk | Severity | Mitigation |
|---|---|---|
| **Product imagery** — client requires real photos, we own none | 🔴 High | Under active investigation. Affiliate feeds licence images to affiliates; Shopify feeds may give images+prices+sizes+stock in one integration. Illustration as durable fallback. |
| **Staleness** — a boot database rots every season | 🟠 Med → 🟡 Low | **Largely defused by the Elite-only cut.** Tens of boots stay correct; thousands cannot. `last_verified` shown publicly. |
| **Competitors close the gap** — Studs adds tiers, FOOTY.COM adds LV | 🟠 Med | Accepted. The gap is a feature set, not a moat. |
| **Nike affiliate restrictions** | 🟠 Med | Confirm early; adidas + multi-retailer feeds may carry it |
| **No traffic** — the real risk for any content site | 🔴 High | Shareable stickers, the LV/RU gap, long-tail per-boot SEO |
| **Trademark** — a site about Nike and adidas | 🟡 Low | Nominative fair use; no brand logos in our identity; brand names stay out of our domain |
| **Scraping legality** in the EU | 🟡 Low | Affiliate feeds first; polite rate — already the `dealScout` standing rule |

---

## 8. Where it lives

- **New repo** under `samoletovs`, registered in `naurolabs/landing-page/projects.json`
  with its own research question.
- **Not** folded into `dealScout` — that stays the private personal engine. The site
  consumes its catalogue as data.
- **Not** folded into `golazo` — that is a logged-in app; this is a public SEO/content
  play. Different jobs. They cross-link.
- Subdomain on `naurolabs.com`, Azure Static Web Apps, matching the lab pattern.

**The research question, in lab format:**

> *Can a structured model of what a product actually is make price comparison honest
> in a market where naming is deliberately ambiguous?*

---

### The stack, inherited from `golazo`

The lab has already proved this exact delivery path, so we copy rather than choose:

React 19.2 · Vite 8 · Tailwind 4.2 · TypeScript 5.9 · **react-i18next 15.7** ·
Framer Motion 12 · Recharts 2.15 · Vitest · Azure Static Web Apps
(`staticwebapp.config.json` + `infrastructure/`)

### DNS reality

`naurolabs.com` is on **Google Cloud DNS**. Apex points at Azure (`20.50.153.39`);
`art.naurolabs.com` is a CNAME to GitHub Pages. So both patterns exist — we add
`broom` as a CNAME to the Azure Static Web App.

---

## 9. Open decisions (need the human)

1. ~~**The name.**~~ ✅ **bRoom** / `broom.naurolabs.com`
2. **Azure account** — 🔴 blocker for deploy only. `az` on this machine is logged into
   the **Microsoft corporate tenant**, not `samoletov@live.com`. A personal deployment
   needs `az login` against the personal tenant. Nothing else is blocked by this.
3. **Scope of v1** — Nike + adidas only, or all six brands already in the catalogue?
4. ~~**Does the Scout ship in v1?**~~ ✅ **Both from day one.** The Archive and the
   Scout launch together.

### What "both from day one" commits us to

This is the more ambitious call, so the consequences should be explicit rather than
discovered later:

| Needed for launch | Why it's real work |
|---|---|
| Affiliate programme approvals | Awin/Tradedoubler applications take days–weeks to approve, and some reject sites with no traffic. **Start these immediately** — they are the longest lead time in the whole plan. |
| Retailer product feeds | Feed formats differ per retailer; each needs a parser and a mapping into our boot identity |
| A running price-history job | Cron + storage, so "cheapest in 45 days" is true on launch day rather than three months later |
| Boot-identity matching at scale | The classifier must resolve thousands of feed titles, not a hand-picked watch list |
| Legal surface | Affiliate disclosure, image licensing per feed terms, GDPR for any alerts |

**The sequencing risk:** the Scout is only honest once price history exists, and price
history only accumulates with time. Mitigation — start collecting prices *now*, in
parallel with building the site, so the log has months of depth by launch. This is the
one task that cannot be parallelised away, so it goes first.
