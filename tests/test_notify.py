"""Unit tests for email rendering helpers (dealscout.notify)."""

from __future__ import annotations

from dataclasses import replace

from dealscout.models import Hunt, Product, Verdict
from dealscout.notify import (
    feedback_base_url,
    markdown_to_html,
    render_report,
    render_shortlist,
    tier_legend,
)
from dealscout.shortlist import Delivery, SourceCoverage


def _signal(url: str = "https://shop.example.com/p", source: str = "Test Store") -> tuple[Product, Verdict]:
    product = Product(
        title="BOSS wool crew",
        category="knitwear",
        price=45.0,
        reference_price=150.0,
        currency="EUR",
        url=url,
        materials={"wool": 1.0},
        brand="BOSS",
        source=source,
    )
    return product, Verdict(True, 42.0, ("70% off", "€45 → must-buy"), "must-buy")


def test_markdown_to_html_makes_feedback_links_clickable():
    body = render_report([_signal()], feedback_base_url="https://courier.example.com/api/feedback")

    html = markdown_to_html(body)

    assert html is not None
    assert '<a href="https://courier.example.com/api/feedback' in html
    assert "👍" in html and "👎" in html
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_feedback_base_url_derives_from_courier_url(monkeypatch):
    monkeypatch.setenv("COURIER_URL", "https://c.example.com/api/send")

    assert feedback_base_url() == "https://c.example.com/api/feedback"


def test_render_report_links_title_and_omits_raw_url():
    body = render_report([_signal(url="https://shop.example.com/boss")])

    # title is a clickable link to the product; no bare URL line hogging space
    assert "[BOSS wool crew — €45](https://shop.example.com/boss)" in body
    assert "\n- https://shop.example.com/boss" not in body


def test_render_report_shows_discount_when_reference_price_present():
    body = render_report([_signal(url="https://shop.example.com/boss")])

    # _signal has reference_price 150 and price 45 -> 70% off
    assert "was €150 (-70%)" in body


def test_render_report_groups_by_store_most_first():
    a = _signal(url="https://s/1", source="Zalando")
    b = _signal(url="https://s/2", source="Zalando")
    c = _signal(url="https://s/3", source="About You")

    body = render_report([a, c, b])

    assert "## Zalando — 2 deal(s)" in body
    assert "## About You — 1 deal(s)" in body
    assert body.index("Zalando") < body.index("About You")  # store with more deals first
    assert " · new" in body  # everything is labelled new


def test_render_report_prioritizes_and_labels_brand_stores():
    boss = _signal(url="https://s/1", source="Hugo Boss")  # brand's own shop (items are BOSS)
    zal_a = _signal(url="https://s/2", source="Zalando")
    zal_b = _signal(url="https://s/3", source="Zalando")

    body = render_report([zal_a, zal_b, boss])

    assert "## Hugo Boss — 1 deal(s) (brand store)" in body
    # brand store leads even though Zalando has more deals
    assert body.index("Hugo Boss") < body.index("Zalando")


def test_markdown_to_html_renders_headings_and_bullets():
    html = markdown_to_html("# Title\n\n- one\n- two\n")

    assert "<h1>Title</h1>" in html
    assert "<li>one</li>" in html


def test_markdown_to_html_renders_a_pipe_table():
    # Tables are off by default in Python-Markdown, so the per-source breakdown would
    # otherwise reach the reader as a paragraph of literal pipe characters.
    html = markdown_to_html("| Source | Rows |\n|---|---:|\n| Pro:Direct | 6 |\n")

    assert "<table>" in html
    assert "<td>Pro:Direct</td>" in html


SHORTLIST_HUNT = Hunt(id="boots", label="Boots", sizes=("37",))
SHORTLIST_TABLE = {
    "prodirectsport.ie": Delivery(label="Pro:Direct (IE)", shipping=7.0),
    "komanda.lv": Delivery(label="komanda.lv (Rīga)", shipping=0.0),
}


def _boot(title: str, price: float, source: str) -> Product:
    return Product(
        title=title,
        category="football_boots",
        price=price,
        reference_price=None,
        currency="EUR",
        url=f"https://{source}/{title}",
        source=source,
        sizes=frozenset({"37"}),
        sizes_known=True,
    )


def test_render_shortlist_states_how_many_rows_each_source_contributed():
    # Diversity nobody can see is indistinguishable from none, and the "found" column is
    # what tells the reader six rows from one shop is the catalogue talking, not the ranker.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
        SourceCoverage("komanda.lv", "komanda.lv (Rīga)", count=2, cheapest=212.0, found=2),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
    )

    assert "### Where these came from" in body
    assert "| Pro:Direct (IE) | 6 | €62 | 15 |" in body
    assert "| komanda.lv (Rīga) | 2 | €212 | 2 |" in body


def test_render_shortlist_warns_when_a_source_yielded_nothing_at_all():
    # Nothing scouted means the reader probably broke — this is the case worth an alarm.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
        SourceCoverage("futbola-apavi.lv", "futbola-apavi.lv", count=0, scouted=0),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
    )

    assert "Nothing at all from futbola-apavi.lv" in body
    assert "| futbola-apavi.lv | 0 |" not in body  # a silent shop is a sentence, not a table row


def test_render_shortlist_does_not_warn_when_a_source_was_read_but_had_nothing_suitable():
    # Measured on futbola-apavi.lv: its Elite stock is adult-only, so it is read correctly
    # every run and matches nothing. Alarming about that weekly would train the reader to
    # ignore the warning, and it would then be worth nothing when a parser really dies.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
        SourceCoverage("futbola-apavi.lv", "futbola-apavi.lv", count=0, scouted=5),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
    )

    assert "⚠️" not in body
    assert "Checked, nothing matching this hunt: futbola-apavi.lv (5 read)" in body


def test_render_shortlist_does_not_say_nothing_matched_when_a_source_was_merely_beaten():
    # A shop whose boots qualified but lost a place on a limited list has not "matched
    # nothing" — saying so would be false. Unreachable while there are fewer sources than
    # rows, but the source list is growing.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
        SourceCoverage("komanda.lv", "komanda.lv", count=0, found=3, scouted=9),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
    )

    assert "⚠️" not in body
    assert "Qualified but did not make the list: komanda.lv (3 qualified)" in body
    assert "nothing matching this hunt" not in body


def test_render_shortlist_omits_the_breakdown_when_there_is_no_coverage():
    body = render_shortlist(SHORTLIST_HUNT, [], [], SHORTLIST_TABLE)

    assert "Where these came from" not in body


def test_short_note_should_leave_a_normal_note_alone():
    from dealscout.notify import short_note

    assert short_note("adidas official · Duntes iela 7") == "adidas official · Duntes iela 7"


def test_short_note_should_trim_a_note_long_enough_to_bury_the_row():
    # This is not hypothetical: a delivery note explaining how a shipping rate had been
    # established was printed beside every one of that source's rows, turning each into a
    # paragraph. Reasoning belongs in a config comment; the row gets the fact.
    from dealscout.notify import NOTE_LIMIT, short_note

    essay = (
        "Latvia falls under 'Other - Europe' in their own rate table, read off "
        "/en/customer-service/ordering on 2026-08-26, and it is the dearest postage here."
    )
    trimmed = short_note(essay)

    assert len(trimmed) <= NOTE_LIMIT
    assert trimmed.endswith("…")
    assert trimmed.startswith("Latvia falls under")


def test_short_note_should_collapse_whitespace_from_a_folded_yaml_note():
    from dealscout.notify import short_note

    assert short_note("ships to LV,\n   cost at checkout") == "ships to LV, cost at checkout"


def test_render_shortlist_warns_when_a_source_yield_falls_sharply():
    # The earlier signal. A source thins out long before it reaches zero, and the
    # zero-only alarm is what produced a wrong diagnosis once already.
    from dealscout.yields import Drop

    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
    ]
    fallen = [Drop(source="sportland.lv", label="Sportland (Rīga)", now=3, baseline=35)]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
        fallen=fallen,
    )

    assert "Yield fell sharply" in body
    assert "Sportland (Rīga) returned 3, usually about 35" in body


def test_render_shortlist_says_nothing_about_yield_when_nothing_fell():
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
        fallen=[],
    )

    assert "Yield fell" not in body


# --- the size-not-published note must describe the shops actually in it ------------


def _unsized(title: str, price: float, source: str) -> Product:
    """A product a shop priced but published no per-size stock for."""
    return replace(_boot(title, price, source), sizes=frozenset(), sizes_known=False)


def test_the_size_not_published_note_should_not_call_a_spanish_shop_riga():
    """The sentence was hardcoded as "Both are in Rīga" and went false when sources grew.

    Caught in a live run: the section led with Fútbol Emotion, in Spain, under a
    sentence telling the owner he could go and visit it.
    """
    table = {**SHORTLIST_TABLE, "futbolemotion.com": Delivery(label="Fútbol Emotion (ES)")}

    body = render_shortlist(
        SHORTLIST_HUNT,
        [],
        [_unsized("A Elite", 64.0, "futbolemotion.com")],
        table,
    )

    assert "in Rīga" not in body
    assert "phoned or visited" not in body


def test_the_size_not_published_note_should_count_the_riga_shops_that_are_there():
    table = {
        **SHORTLIST_TABLE,
        "futbolemotion.com": Delivery(label="Fútbol Emotion (ES)"),
        "sportland.lv": Delivery(label="Sportland (Rīga)", pickup=True),
    }

    body = render_shortlist(
        SHORTLIST_HUNT,
        [],
        [
            _unsized("A Elite", 64.0, "futbolemotion.com"),
            _unsized("B Elite", 104.0, "sportland.lv"),
        ],
        table,
    )

    assert "One is in Rīga, so it can also be phoned or visited." in body


def test_the_size_not_published_note_should_say_all_when_every_shop_is_local():
    table = {
        "sportland.lv": Delivery(label="Sportland (Rīga)", pickup=True),
        "teamsport.lv": Delivery(label="teamsport.lv (Rīga)", pickup=True),
    }

    body = render_shortlist(
        SHORTLIST_HUNT,
        [],
        [_unsized("A Elite", 104.0, "sportland.lv"), _unsized("B Elite", 120.0, "teamsport.lv")],
        table,
    )

    assert "All of them are in Rīga" in body


# --- the redesign: a phone-readable row, and the gloss said once --------------------
#
# These pin properties, not phrasing. "The gloss appears at most once" survives any
# rewording of the gloss; "RRP €220, -61% · cheapest…" did not, and broke on the first
# rewrite. The trap that cost a regression this week was a fixture that restated a value
# instead of relating two things — so these relate the count of the gloss to the number of
# rows, and the size of a row to a phone line, rather than asserting an exact sentence.

GLOSS = "top of the junior range"


def _tier_boot(title: str, price: float, source: str, tier: str, *, sizes_known: bool = True) -> Product:
    """A boot the catalogue has classified, so the tier label and its gloss are in play."""
    return replace(
        _boot(title, price, source),
        attrs={
            "tier": tier,
            "generation_status": "current",
            "generation_year": "2024",
            "soleplate": "FG",
            "silo": "f50",
            "fit": "junior",
        },
        sizes_known=sizes_known,
    )


def _junior_shortlist() -> str:
    junior = [
        _tier_boot(f"adidas F50 Elite {i} FG", 60.0 + 10 * i, "prodirectsport.ie", "junior-flagship")
        for i in range(8)
    ]
    return render_shortlist(SHORTLIST_HUNT, junior, [], SHORTLIST_TABLE)


def test_the_tier_gloss_is_said_at_most_once_however_many_rows_carry_the_label():
    # The measured bloat: the same sentence appeared on all 15 junior rows, ~11% of the
    # whole email. It is good writing worth reading once; a legend is where it belongs.
    body = _junior_shortlist()

    assert body.count("junior flagship") >= 8  # the label is still on every row
    assert body.count(GLOSS) == 1  # the explanation is not


def test_the_gloss_is_silent_when_no_row_needs_explaining():
    # An all-adult week: "adult flagship" is self-describing, so nothing is glossed.
    adult = [_tier_boot("adidas Predator Elite FG", 120.0, "komanda.lv", "adult-flagship")]
    body = render_shortlist(SHORTLIST_HUNT, adult, [], SHORTLIST_TABLE)

    assert GLOSS not in body
    assert tier_legend([{"tier": "adult-flagship"}]) == ""


def test_a_row_no_longer_dumps_the_machine_attribute_chain():
    # "FG · f50 · junior" is engine output nobody buys on; the tier label and the size
    # already carry the decision. The silo/fit tokens must not reach the reader — checked
    # on the fact lines, not the whole body, since a URL slug may legitimately contain them.
    body = _junior_shortlist()

    for line in _fact_lines(body):
        assert " · f50" not in line and "· f50 ·" not in line
        assert " · junior" not in line and "· junior ·" not in line


def _fact_lines(body: str) -> list[str]:
    """The human-facing detail lines of a shortlist: the indented facts under each row,
    excluding the feedback line, whose URLs are an unavoidable cost paid once per row."""
    return [
        ln
        for ln in body.splitlines()
        if ln.startswith("  - ") and "feedback" not in ln and "seen it?" not in ln
    ]


def test_no_fact_line_wraps_past_a_couple_of_phone_lines():
    # A phone shows ~40 chars per line. The old detail line averaged 194 and ran to 223 —
    # five wrapped lines per boot, twenty times. Cap the fact lines (delivery notes aside)
    # so a row stays glanceable. The feedback URLs are measured separately; they are the
    # one thing plain text cannot shrink.
    junior = [
        _tier_boot(f"adidas F50 Elite {i} FG", 60.0 + 10 * i, "prodirectsport.ie", "junior-flagship")
        for i in range(6)
    ]
    body = render_shortlist(SHORTLIST_HUNT, junior, [], SHORTLIST_TABLE)

    for line in _fact_lines(body):
        assert len(line) <= 160, line


def test_feedback_links_survive_in_both_plain_text_and_html():
    # Constraint 2: the 👍/👎 loop must not be lost. Plain text keeps the honest URL;
    # HTML keeps it too, behind a pill — same link, both ways.
    junior = [_tier_boot("adidas F50 Elite FG", 62.0, "prodirectsport.ie", "junior-flagship")]
    base = "https://courier.example.com/api/feedback"
    body = render_shortlist(SHORTLIST_HUNT, junior, [], SHORTLIST_TABLE, feedback_base_url=base)

    assert body.count(f"({base}?p=dealscout&v=up") == 1
    assert body.count(f"({base}?p=dealscout&v=down") == 1

    html = markdown_to_html(body)
    assert html.count("v=up") == 1 and html.count("v=down") == 1
    assert "👍" in html and "👎" in html


def test_html_renders_feedback_as_a_compact_styled_pill_not_a_naked_url():
    # The HTML is what he actually sees; there the giant URL hides behind a tappable pill.
    junior = [_tier_boot("adidas F50 Elite FG", 62.0, "prodirectsport.ie", "junior-flagship")]
    base = "https://courier.example.com/api/feedback"
    body = render_shortlist(SHORTLIST_HUNT, junior, [], SHORTLIST_TABLE, feedback_base_url=base)

    html = markdown_to_html(body)

    # the pill is a styled anchor, and the URL is an href attribute, not visible text
    assert "border-radius" in html
    assert 'style="display:inline-block' in html


def test_html_keeps_the_honest_caveats_and_coverage_warnings():
    # Constraint 1: compress caveats, never delete them. A broken-reader alarm and the
    # size-not-published note must still reach the HTML reader.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=1, cheapest=62.0, found=3),
        SourceCoverage("futbola-apavi.lv", "futbola-apavi.lv", count=0, scouted=0),
    ]
    body = render_shortlist(
        SHORTLIST_HUNT,
        [_tier_boot("adidas F50 Elite FG", 62.0, "prodirectsport.ie", "junior-flagship")],
        [_unsized("Nike Elite", 89.0, "sportland.lv")],
        {**SHORTLIST_TABLE, "sportland.lv": Delivery(label="Sportland (Rīga)", pickup=True)},
        coverage=coverage,
    )
    html = markdown_to_html(body)

    assert "Nothing at all from futbola-apavi.lv" in html
    assert "not per-size stock" in html  # the size-not-published honesty survives
