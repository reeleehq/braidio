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
