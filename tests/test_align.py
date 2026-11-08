"""``braidio.align``: time an authored script on audio braidio did not render (#212)."""

from __future__ import annotations

import pytest

import braidio
from braidio import Alignment, align_script, captions_from_cues
from braidio.script import Narration, Script

TEXT = "Un deux trois. Quatre cinq.\n\nMatte. Six sept huit.\n\nNeuf."


def _words(spec):
    return [{"text": t, "start": s, "end": e, "type": "word"} for t, s, e in spec]


#: What a recogniser heard: "Matte." came out as "mate" and is not a script word.
HEARD = _words(
    [
        ("Un", 1.0, 1.2),
        ("deux", 1.2, 1.5),
        ("trois.", 1.5, 2.0),
        ("Quatre", 2.2, 2.6),
        ("cinq.", 2.6, 3.0),
        ("mate", 5.0, 5.3),
        ("Six", 5.5, 5.8),
        ("sept", 5.8, 6.1),
        ("huit.", 6.1, 6.5),
        ("Neuf.", 9.0, 9.4),
    ]
)


def test_cues_carry_the_authored_words_at_measured_times():
    a = align_script(TEXT, HEARD, duration_s=10.0)
    assert [c.text for c in a.cues] == [
        "Un deux trois.",
        "Quatre cinq.",
        "Matte.",
        "Six sept huit.",
        "Neuf.",
    ]
    assert (a.cues[0].start_s, a.cues[0].end_s) == (1.0, 2.0)
    assert (a.cues[3].start_s, a.cues[3].end_s) == (5.5, 6.5)
    assert all(x.end_s <= y.start_s for x, y in zip(a.cues, a.cues[1:]))


def test_a_chunk_the_recogniser_missed_is_placed_between_its_neighbours():
    a = align_script(TEXT, HEARD)
    assert a.unmatched == ("Matte.",)
    missed = next(c for c in a.cues if c.text == "Matte.")
    assert 3.0 <= missed.start_s < missed.end_s <= 5.5


def test_the_timeline_is_contiguous_and_feeds_the_video_planner():
    a = align_script(TEXT, HEARD, duration_s=10.0)
    beats = a.timeline.beats
    assert len(beats) == 3  # one per paragraph
    assert beats[0].start == 0.0 and a.timeline.duration == pytest.approx(10.0)
    assert [b.start for b in beats[1:]] == [b.end for b in beats[:-1]]
    # a paragraph starts where its first cue does (the first also holds the lead-in)
    assert beats[1].start == pytest.approx(a.cues[2].start_s)
    spans = braidio.video.plan_spans(a.timeline)
    assert spans[0].start == 0.0 and spans[-1].end == pytest.approx(10.0)


def test_a_script_is_timed_by_its_narration_beats():
    script = Script(
        title="t",
        id_slug="01",
        beats=[
            Narration("Un deux trois. Quatre cinq."),
            braidio.SegmentBeat(reference="a quoted clip"),
            Narration("Matte. Six sept huit.\n\nNeuf."),
        ],
    )
    by_beat = align_script(script, HEARD, unit="beat")
    assert len(by_beat.timeline.beats) == 2
    assert len(align_script(script, HEARD).timeline.beats) == 3


def test_unspoken_markup_is_not_aligned_and_spacing_entries_are_ignored():
    words = HEARD + [{"text": " ", "start": 0.0, "end": 0.1, "type": "spacing"}]
    text = "Un deux trois. [pause] <break time='1s'/> Quatre cinq."
    a = align_script(text, words)
    assert [c.text for c in a.cues] == ["Un deux trois.", "Quatre cinq."]


def test_normalize_is_a_seam():
    # a normaliser that keeps accents tells "été" and "ete" apart
    words = _words([("ete", 0.0, 0.5)])
    exact = align_script("été", words, normalize=str.split)
    assert exact.unmatched == ("été",)
    assert align_script("été", words).unmatched == ()


@pytest.mark.parametrize(
    "bad, match",
    [
        (dict(text="x", words=[]), "at least one word"),
        (dict(text=" ", words=HEARD), "no text"),
        (dict(text="x", words=HEARD, unit="verse"), "unit must be"),
    ],
)
def test_bad_input_is_refused_by_name(bad, match):
    with pytest.raises(ValueError, match=match):
        align_script(
            bad["text"],
            bad["words"],
            **({"unit": bad["unit"]} if "unit" in bad else {}),
        )


def test_captions_from_cues_wraps_and_holds_without_overlap():
    a = align_script(TEXT, HEARD)
    srt = captions_from_cues(a.cues, max_chars=10)
    assert "Un deux\ntrois." in srt
    assert isinstance(a, Alignment)
    # held 0.6 s past the last word, but never into the next cue (3.2 - 0.05)
    assert "00:00:02,200 --> 00:00:03,150" in srt


def test_non_speech_entries_are_dropped_before_matching():
    from braidio.align import _speech

    kept = _speech(
        [
            {"text": "oui", "start": 0, "end": 1, "type": "word"},
            {"text": "(rire)", "start": 1, "end": 2, "type": "audio_event"},
            {"text": " ", "start": 2, "end": 3, "type": "spacing"},
            {"word": "non", "start": 3, "end": 4},  # a foley-style word list
            {"text": "sans", "start": None, "end": 5},  # no time: unusable
        ]
    )
    assert [t for t, _, _ in kept] == ["oui", "non"]


def test_cues_never_overlap_even_when_matched_words_do():
    # two chunks matched by words whose times interleave (a sloppy aligner)
    words = _words([("un", 1.0, 3.0), ("deux", 2.0, 2.5)])
    a = align_script("Un. Deux.", words)
    assert a.cues[0].end_s <= a.cues[1].start_s
