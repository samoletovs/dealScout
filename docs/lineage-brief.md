# bRoom — the Lineage brief (2026-09-01)

**The direction changed.** Read this before touching anything.

---

## What the owner asked for, in his words

> "Still missing a lot of information — prices not enough, pictures not enough, details
> about the boot and the history not enough. You need to find the picture for **each of
> these years** so we see how this model evolved. So persons can really understand the
> history of the boot — why it was created, what innovation they made, who wore it.
> Build a database of the elite boots. It should be interesting to read, for my son and
> his teammates. More educational site. Prices as second part."

Two consequences:

1. **The Archive is the product. The Scout is the second feature.** The site is an
   educational history of the elite football boot. Price stays, stays honest, and stops
   being the headline. Nobody returns weekly to a price table. People do return to
   something they are learning.

2. **The unit of content is the generation, not the boot.** Predator 1994 →
   Touch → Accelerator → Mania → … → 26, each with a year, what actually changed, who
   wore it, and **a photograph of that generation**. A timeline without a picture per
   step is a list of names. With pictures it is the reason the site exists.

**Audience: a fourteen-year-old who cares enormously.** He wears 37–37.5, which is
already an adult size. Write for him, not for his parent. If a sentence needs a glossary
to parse, rewrite the sentence.

---

## The scope rule, and the trap inside it

The owner said "only elite boots, RRP over €200". That is right for **what we sell** and
catastrophic for **what we teach**. Measured 2026-09-01:

| Boot | Launch RRP | Under a literal €200 gate |
|---|---|---|
| 1979 Copa Mundial | €140 | **dropped** — the best-selling football boot ever made |
| 1994 Predator | *no euro price* | **dropped** — the euro did not exist |
| 1998 Mercurial R9 | *no euro price* | **dropped** — Ronaldo's silver boot |
| 2002 Predator Mania | *no euro price* | **dropped** — widely called the greatest ever |

So the rule splits — the same shape of mistake this project has caught four times
already, where a rule correct for one question silently answers a different one:

- **Archive scope** — *was this the flagship of its own era?* Price-blind, era-relative.
  This is what you research. A 1994 boot is in scope.
- **Scout scope** — *adult flagship, `launch_rrp_eur` ≥ €200, still sold.* This is what
  we monitor and quote a price for. No junior flagship, no academy, no league, no
  takedowns on the site's buyable surface.

Those tiers stay in `data/football_boots.yaml` — the engine still needs them to classify
a title correctly and to refuse a false "75% off". They just leave the site.

---

## The data contract (already merged, PR #63)

`data/broom/lineage/<silo>.yaml`, one file per silo. **You own your file and no other.**

```yaml
last_verified: "2026-09"
category: broom_lineage
brand: adidas
silo: predator

generations:
  - brand: adidas
    silo: predator
    sequence: 1          # display order; must agree with year order
    name: Predator       # what the BRAND called it, not a retailer
    year: 1994
    photo: predator-og-1994   # an id in data/broom/photos.yaml, or UNVERIFIED
    innovation: >-
      What actually CHANGED, in plain language. This is the heart of the record.
    why: >-
      The problem it was trying to solve. Optional, but this is the story.
    players: UNVERIFIED  # who wore it, notably — sourced
    sources:
      - https://...      # at least one http source, ABOUT THIS GENERATION
```

Run `python -m pytest tests/test_broom_lineage.py -q`. Eight guards, each verified to
fire by fault injection. They enforce:

- required fields; at least one `http` source per entry
- years 1979–2027 (a typo silently reorders a timeline)
- `sequence` must agree with the years it displays — **gaps are fine**, a
  half-researched silo is a normal state; a contradiction is not
- silos must exist in `data/football_boots.yaml` (one boot fact, one home)
- `photo` is an **id into `photos.yaml`**, never a copy of the licence metadata
- **a source must not cite a different generation of the same silo**

---

## The two rules that will bite hardest

### 1. A source about a neighbouring generation is not a source

The main dataset already had **eight rows citing a review of a different boot**. Across
a twenty-generation lineage this is nearly invisible to a reader and completely
invisible to a type check. A "Predator 24 review" does not support a claim about the
Predator 25, however similar they look.

### 2. A photo must be of the generation it sits next to

A Mania photographed beside the Accelerator entry teaches the reader something false.
`UNVERIFIED` always beats approximately-right. **An honest gap in a timeline is
interesting in itself** — "no freely-licensed photograph of the 2000 Precision exists"
is a true and rather charming fact.

---

## Photography — read this or you will breach a licence

This project already committed four adidas product photographs to a public repo. The
repo had to be made private and it is still private. Do not repeat it.

**Banned:** retailer photos, brand press images, anything scraped from nike.com,
adidas.com, Pro:Direct, Unisport, SportsDirect. Nike's own terms: *"permission is rarely
granted."*

**Permitted:** Wikimedia Commons files under **CC BY, CC BY-SA, CC0, or public domain**,
each licence **verified file-by-file via the Commons API** — never assumed from a search
result, never inferred from another file by the same uploader.

Four conditions, all mandatory:

1. **Attribution is a licence condition, not a courtesy.** Author + licence + link.
2. **Fetch at build time. Never commit an image binary.** `scripts/fetch-photos.mjs`
   already does this; add entries to `photos.yaml`, not files to git.
3. **Verify each licence from the API**, and record `licence`, `licence_url`, `author`,
   `source_url`, `width`, `height`.
4. **Caption honestly.** A 1994 Predator is not a Predator 26. If the photo is a
   player-issue pair, say whose.

Search noise is real: a previous search for "F50" returned **a bus**. Confirm the object
in the image is the boot you think it is.

### The four-check gate, learned the hard way on 2026-09-01

Every one of these was added because something got past the checks before it. Run all
four on every candidate, in order.

**1. Verify the licence at source, file by file.** Via the Commons API, never inferred
from a search result and never from another file by the same uploader.

**2. Confirm the object is the boot.** The bus was real.

**3. Ask whether the photographed *thing* is itself someone else's copyright.**
A CC-licensed photograph of an advertisement, poster, packaging, magazine spread, product
render or museum display panel **does not clear the artwork inside it** — the photographer
never held those rights to give away. Publishing it puts a brand's own creative on our
pages under a licence the brand never granted. This is the same mistake that made this
repo private, wearing a Creative Commons badge.

**4. Look at the pixels.** Do not stop at the metadata. A Flickr file titled *"Adidas
Adizero f50 football boot"*, CC BY 2.0, genuinely of the f50, passed checks 1 and 2 —
and turned out to be a photograph of an **INTERSPORT bus-stop poster**, retailer logo and
all. Only downloading it and looking revealed that.

### Four more rules, same day

- **Read the generation off the photo, not off its date.** A file shot in 2024 is not
  evidence of a 2024 generation.
- **A filename is not a source.** `legend v.jpg` whose own description says only "one of
  the models from the 2015 range" does not identify a generation. The uploader's written
  description outranks their file naming, always.
- **Grep `photos.yaml` for the `commons_file` before proposing one.** Everyone searching
  a silo lands on the same handful of images; two sessions proposed files we already had.
- **Walk the category tree, not keywords.** This is the big one. Three sweeps concluded
  the free pool was exhausted and that phantom and f50 would never have a photograph. All
  three searched by **keyword**. Walking `Category:Association football boots` and the
  Nike/adidas subcategories returns **169 football-specific files, 168 freely licensed**,
  because Commons filenames do not contain the words anyone would search for. That is how
  phantom and f50 went from zero to a 6000px photograph each.

> A search that finds nothing has told you either that the archive is empty, or that your
> method cannot see. Check which before you report the first.

**And do not bend the spine to fit an image.** Two clean, well-licensed Tiempo photographs
were rejected because they showed the *Mystic*, a mid-tier line, not a flagship
generation. Adding a mid-tier boot to a flagship lineage because a photograph happens to
exist would reproduce, in our own data, the exact tier confusion this site was built to
correct.

**Coverage today:** 13 photos. Predator is rich (1994, 1996, 1998, 2002, 2004, 2006,
2012 ×2, 2018 — already verified and merged). Every other silo has **one**. Finding
licensed photographs for the other six lineages is the single highest-value thing you
can do, because the owner has said plainly that without pictures the site does not make
sense.

---

## What "good" looks like for one generation

Not this:

> **Predator Mania (2002)** — A classic Predator with a leather upper.

This:

> **Predator Mania — 2002**
> The one people still ask adidas to bring back, and in 2017 adidas did. Kangaroo
> leather over the fold-over tongue, with the rubber Predator elements slimmed down from
> the slabs of the Accelerator into a finer strip — enough swerve, far less bulk. Zidane
> wore them through the 2002 season and in the Champions League final volley. Sold out
> so completely on its 2017 re-release that adidas re-ran it again.
> *[photo: Zidane's own pair, CC BY-SA 4.0, Pangalau]*

The difference is that the second one **tells him something he did not know** and
**every claim in it can be checked**.

---

## Ownership — do not touch another session's file

| Session | Owns | Must not touch |
|---|---|---|
| lineage-predator | `data/broom/lineage/predator.yaml` | any other lineage file |
| lineage-copa | `data/broom/lineage/copa.yaml` | " |
| lineage-f50 | `data/broom/lineage/f50.yaml` | " |
| lineage-superfly | `data/broom/lineage/mercurial-superfly.yaml` | " |
| lineage-vapor | `data/broom/lineage/mercurial-vapor.yaml` | " |
| lineage-tiempo | `data/broom/lineage/tiempo.yaml` | " |
| lineage-phantom | `data/broom/lineage/phantom.yaml` | " |
| photos | `data/broom/photos.yaml` | lineage files |
| site | `bRoom/` timeline UI | `dealScout/data/**` |

Everyone may **add** to `photos.yaml`; coordinate through the lead if you collide.

---

## Non-negotiables inherited from this project

- **Never claim what cannot be proved.** `UNVERIFIED` is a respectable answer. An
  invented weight, an unsourced "most popular boot ever", a guessed release year — these
  are the failure this whole project exists to avoid.
- **Never commit an image binary.**
- **A branch that deletes lines you did not intend to delete is stale.** Rebase on
  `origin/main` before opening a PR. This trap has been hit repeatedly: the tell is a
  large *deletion* count on an add-only branch.

  **But measure it with a three-dot diff, or you will chase ghosts.** `git diff main
  branch` compares the two *tips* and shows every file main gained since you branched as
  a "deletion" — a stale-but-harmless branch showed **1,346 deletions** this way,
  including three whole silo files. `git diff main...branch` compares against the
  merge-base and showed **24**. A merge keeps files that were added on main and untouched
  on the branch; `git merge-tree` confirms it. **`gh pr view --json additions,deletions`
  reports the three-dot number**, which is why the tell is reliable on real PRs and
  misleading on raw tip comparisons.

  The real risk from a stale branch is subtler than deletion: if your file changed on
  **both** sides of the merge-base, a merge can auto-merge hunk-by-hunk and silently
  reintroduce old values on lines main has since fixed. That is what nearly un-fixed a
  release year here.
- **"Merged", "live" and "correct" are three different states.** A pin can move forward,
  list every file, pass every guard, and still publish a page full of gaps because the
  *content* at that ref was not ready. That shipped here. Check what a new page renders,
  not just that it renders.
- **Add a test that fails without your change**, and say in the PR how you verified it.
- **Do not open a `[WIP]` PR.** If blocked, comment on the issue and stop.
- Run `python -m pytest tests/ -q` and `python -m dealscout.eval` before any PR.
  Currently: **700 passing, eval 100%**.
