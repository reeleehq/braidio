"""script_from_markdown: structure, speakable text, respelling, tag-only check (#107)."""

import pytest

import braidio
from braidio import Narration, Script, script_from_markdown, untagged_differences

ESSAY = """---
title: "Agile, Reformed"
subtitle: A short history
---

# Part one

![a diagram](img/d.png)

Read **the** [spec](https://example.com/spec)[^1] and `AGENTS.md` now.

- first point
- second point

> A quoted line.

```python
never_read_this()
```

---

[^1]: The *footnote* text.
"""


def _by_style(script):
    return [(b.style, b.text) for b in script.beats]


def test_structure_and_speakable_text():
    script = script_from_markdown(ESSAY)
    assert script.title == "Agile, Reformed"
    assert script.id_slug == "agile-reformed"
    assert _by_style(script) == [
        ("title", "Agile, Reformed. A short history."),
        ("heading", "Part one"),
        ("para", "Read the spec and AGENTS.md now."),
        ("list_item", "first point"),
        ("list_item", "second point"),
        ("quote", "A quoted line."),
        ("footnote", "A footnote. The footnote text."),
    ]


def test_gaps_come_from_the_table_and_are_overridable():
    script = script_from_markdown(
        ESSAY, gaps={**braidio.markdown_script.DEFAULT_GAPS, "heading": 9.0}
    )
    gaps = {b.style: b.lead_gap_s for b in script.beats}
    assert gaps["heading"] == 9.0 and gaps["para"] == 0.35 and gaps["title"] == 0.0


def test_image_captions_and_footnote_drop():
    script = script_from_markdown(ESSAY, images="caption", footnotes="drop")
    texts = [b.text for b in script.beats]
    assert "a diagram" in texts
    assert not any(b.style == "footnote" for b in script.beats)


def test_respell_applies_to_beats_not_the_source_and_prefers_longest_key():
    script = script_from_markdown(
        "Use AGENTS.md and AGENTS.",
        respell={"AGENTS": "agents", "AGENTS.md": "AGENTS dot M D"},
    )
    assert script.beats[0].text == "Use AGENTS dot M D and agents."


def test_voice_threads_to_every_beat_and_no_front_matter_uses_first_heading():
    script = script_from_markdown("# Hello World\n\nBody.", voice="v1")
    assert script.title == "Hello World" and script.id_slug == "hello-world"
    assert {b.voice for b in script.beats} == {"v1"}
    assert [b.style for b in script.beats] == ["heading", "para"]


@pytest.mark.parametrize("kw", [{"footnotes": "x"}, {"images": "x"}])
def test_bad_modes_are_refused(kw):
    with pytest.raises(ValueError):
        script_from_markdown("x", **kw)


def test_untagged_differences_accepts_tags_and_catches_edits():
    plain = script_from_markdown("One two.\n\nThree four.")
    tagged = Script(
        plain.title,
        plain.id_slug,
        [Narration("[pause] One  two."), Narration("[dryly] Three four.")],
    )
    assert untagged_differences(plain, tagged) == []
    edited = Script(plain.title, plain.id_slug, [Narration("One 2."), plain.beats[1]])
    (mismatch,) = untagged_differences(plain, edited)
    assert (mismatch.index, mismatch.after) == (0, "One 2.")
    dropped = Script(plain.title, plain.id_slug, plain.beats[:1])
    assert [m.index for m in untagged_differences(plain, dropped)] == [1]


# --- regression corpus: markdown the hand-rolled parser got wrong (#107 review) --

CORPUS = {
    "snake_case": (
        "Call snake_case_name now.",
        {},
        [("para", "Call snake_case_name now.")],
    ),
    "arithmetic": ("It is 2 * 3 * 4.", {}, [("para", "It is 2 * 3 * 4.")]),
    "link_with_parens_url": (
        "See [the page](https://en.wikipedia.org/wiki/Foo_(bar)) here.",
        {},
        [("para", "See the page here.")],
    ),
    "reference_links": (
        "Read [the spec][s] and [ref].\n\n[s]: https://x.y/spec\n[ref]: https://x.y/r",
        {},
        [("para", "Read the spec and ref.")],
    ),
    "consecutive_and_multi_paragraph_footnotes": (
        "Body[^b][^a].\n\n[^a]: Alpha one.\n\n    Alpha two.\n\n[^b]: Beta.",
        {},
        [
            ("para", "Body."),
            ("footnote", "A footnote. Beta."),
            ("footnote", "A footnote. Alpha one. Alpha two."),
        ],
    ),
    "setext_heading": (
        "Title here\n==========\n\nBody.",
        {},
        [("heading", "Title here"), ("para", "Body.")],
    ),
    "hashtag_is_para": (
        "#hashtag is not a heading",
        {},
        [("para", "#hashtag is not a heading")],
    ),
    "closed_atx_heading": ("## H ##\n\nx", {}, [("heading", "H"), ("para", "x")]),
    "html_and_entities": (
        "Tom &amp; <b>Jerry</b> &copy; 2020",
        {},
        [("para", "Tom & Jerry © 2020")],
    ),
    "escaped_asterisks": (
        r"Not \*emphasis\* here.",
        {},
        [("para", "Not *emphasis* here.")],
    ),
    "indented_code": (
        "Before.\n\n    code_line()\n\nAfter.",
        {},
        [("para", "Before."), ("para", "After.")],
    ),
    "inline_image_caption": (
        "Look ![a red *door*](d.png) there.",
        {"images": "caption"},
        [("para", "Look a red door there.")],
    ),
    "inline_image_dropped": (
        "Look ![a red door](d.png) there.",
        {},
        [("para", "Look there.")],
    ),
    "badge_image_link": (
        "[![build](https://b.svg)](https://ci)\n\nText.",
        {},
        [("para", "Text.")],
    ),
    "autolink": ("Visit <https://example.com> today.", {}, [("para", "Visit today.")]),
    "strikethrough": ("It was ~~not~~ fine.", {}, [("para", "It was not fine.")]),
    "task_list": (
        "- [ ] todo one\n- [x] done two",
        {},
        [("list_item", "todo one"), ("list_item", "done two")],
    ),
    "crlf_front_matter": (
        "---\r\ntitle: Win\r\n---\r\n\r\nLine one\r\nline two.",
        {},
        [("title", "Win."), ("para", "Line one line two.")],
    ),
    "table": ("| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter.", {}, [("para", "After.")]),
    "html_comment": (
        "<!-- hidden -->\n\nSeen. <!-- inline --> too.",
        {},
        [("para", "Seen. too.")],
    ),
    "wrapped_list_and_quote": (
        "- one\n  wrapped\n- two\n\n> quoted\n> wrapped\n\n> second para",
        {},
        [
            ("list_item", "one wrapped"),
            ("list_item", "two"),
            ("quote", "quoted wrapped"),
            ("quote", "second para"),
        ],
    ),
    "loose_list_item_is_one_beat": (
        "- first para\n\n  second para\n- next",
        {},
        [("list_item", "first para second para"), ("list_item", "next")],
    ),
    "footnote_lead_in": (
        "x[^1]\n\n[^1]: y",
        {"footnote_lead_in": "Note:"},
        [("para", "x"), ("footnote", "Note: y")],
    ),
    "respell_word_boundaries": (
        "AI SAID AI.",
        {"respell": {"AI": "A I"}},
        [("para", "A I SAID A I.")],
    ),
}


@pytest.mark.parametrize("md, kw, expected", CORPUS.values(), ids=CORPUS.keys())
def test_regression_corpus(md, kw, expected):
    assert _by_style(script_from_markdown(md, **kw)) == expected


def test_title_and_id_slug_overrides_and_plain_heading_title():
    script = script_from_markdown("# The *Real* `Name`\n\nx")
    assert script.title == "The Real Name" and script.id_slug == "the-real-name"
    script = script_from_markdown("# H\n\nx", title="Mine", id_slug="s1")
    assert (script.title, script.id_slug) == ("Mine", "s1")


def test_partial_gaps_merge_with_defaults_which_are_read_only():
    script = script_from_markdown(ESSAY, gaps={"heading": 9.0})
    gaps = {b.style: b.lead_gap_s for b in script.beats}
    assert gaps["heading"] == 9.0 and gaps["para"] == 0.35
    with pytest.raises(TypeError):
        braidio.markdown_script.DEFAULT_GAPS["heading"] = 1.0


def test_untagged_differences_catches_a_deleted_bracketed_word():
    before = Script("t", "t", [Narration("Read the [note] first.")])
    after = Script("t", "t", [Narration("[pause] Read the first.")])
    assert [m.index for m in untagged_differences(before, after)] == [0]
    kept = Script("t", "t", [Narration("[pause] Read the [note] first.")])
    assert untagged_differences(before, kept) == []


def test_untagged_differences_compares_non_narration_beats():
    from braidio import SceneBreak

    before = Script("t", "t", [Narration("A."), SceneBreak()])
    after = Script("t", "t", [Narration("A.")])
    assert untagged_differences(before, after) == [
        braidio.markdown_script.TagMismatch(-1, "1", "0")
    ]


def test_script_to_json_round_trips_all_beat_types():
    from braidio import Dialogue, SceneBreak, SegmentBeat
    from braidio.mcp._helpers import script_from_json, script_to_json

    script = Script(
        "T",
        "t",
        [
            Narration("Hi.", style="heading", lead_gap_s=1.3),
            SegmentBeat(reference="a line", label="clip"),
            Dialogue(turns=(("host", "Hello."), ("guest", "Hi."))),
            SceneBreak(),
        ],
    )
    assert script_from_json(script_to_json(script)) == script
