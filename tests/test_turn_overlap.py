"""Speaker interruption (WeaveConfig.overlap_turn_s, Hamilton#22).

The layout is pure, so most of the contract is pinned on ``layout_placed`` with
durations and speakers alone; one offline render proves the mixer honours the same
offsets (``narrate`` is replaced by a tone writer — no network, no spend).
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

import braidio
from braidio.weave import (
    TimelineItem,
    duck_regions,
    interrupting_boundaries,
    layout_placed,
    weave_timeline,
)
from braidio.weave_config import MAX_OVERLAP_TURN_S, TurnOverlap, WeaveConfig

from _audio import decode, needs_ffmpeg, rms, silence, tone

XF = 0.12  # the crossfade the layout is called with


def _layout(speakers, durs=None, *, overlap=None, kinds=None):
    n = len(speakers)
    return layout_placed(
        kinds or ["narration"] * n,
        durs or [2.0] * n,
        ["sequential"] * n,
        clip_edge_overlap_s=0.5,
        narration_crossfade_s=XF,
        speakers=speakers,
        overlap=overlap,
    )


def test_off_by_default_is_the_historical_crossfade_layout():
    on = _layout(["a", "b", "a"], overlap=TurnOverlap(max_s=0.0))
    assert on == _layout(["a", "b", "a"], overlap=None) == [0.0, 1.88, 3.76]


def test_overlap_only_when_the_voice_changes():
    starts = _layout(["a", "a", "b"], overlap=TurnOverlap(max_s=0.3, jitter=0.0))
    assert starts[1] == pytest.approx(2.0 - XF)  # same voice: crossfade only
    assert starts[2] == pytest.approx(starts[1] + 2.0 - 0.3)  # speaker change


def test_jitter_is_seeded_and_bounded():
    ov = TurnOverlap(max_s=0.4, jitter=0.5, seed=3)
    first = _layout(["a", "b", "a", "b"], overlap=ov)
    assert first == _layout(["a", "b", "a", "b"], overlap=ov)  # reproducible
    other = _layout(["a", "b", "a", "b"], overlap=TurnOverlap(0.4, 0.5, 0.0, 4))
    assert first != other  # the seed matters
    gaps = [2.0 - (b - a) for a, b in zip(first, first[1:])]
    assert all(0.2 - 1e-9 <= g <= 0.4 + 1e-9 for g in gaps)  # max_s*(1-jitter)..max_s


def test_overlap_is_capped_by_the_turn_lengths():
    starts = _layout(["a", "b"], [2.0, 0.15], overlap=TurnOverlap(0.4, 0.0))
    assert 2.0 - starts[1] == pytest.approx(0.15)  # next turn only 0.15s long
    starts = _layout(["a", "b"], [0.15, 2.0], overlap=TurnOverlap(0.4, 0.0))
    assert starts[1] >= 0.0 and 0.15 - starts[1] == pytest.approx(0.15)  # prev too


def test_never_more_than_two_voices_at_once():
    # three short turns by alternating voices, overlap larger than the middle turn
    durs = [1.0, 0.2, 1.0]
    starts = _layout(["a", "b", "c"], durs, overlap=TurnOverlap(0.4, 0.0))
    assert starts[2] >= starts[0] + durs[0]  # turn 3 never starts under turn 1


def test_a_same_voice_turn_never_starts_under_the_part_two_back():
    # b interrupts a with a short turn, then b continues: without a floor on the
    # b→b crossfade boundary, b talks over itself while a's tail still plays.
    durs = [2.0, 0.3, 2.0]
    starts = _layout(["a", "b", "b"], durs, overlap=TurnOverlap(0.4, 0.0))
    assert starts[:2] == pytest.approx([0.0, 1.7])
    assert starts[2] >= starts[0] + durs[0]


def test_no_third_part_of_any_kind_at_a_non_clip_boundary():
    # a very short sting between two turns: the crossfade alone would start the
    # next turn under both the sting and the first turn (three parts at once).
    kinds, durs = ["narration", "sting", "narration"], [2.0, 0.05, 2.0]
    ov = TurnOverlap(0.4, 0.0)
    off = _layout(["a", None, "b"], durs, kinds=kinds)
    assert off[2] < durs[0]  # the historical layout, unchanged with overlap off
    on = _layout(["a", None, "b"], durs, overlap=ov, kinds=kinds)
    assert on[2] >= on[0] + durs[0]


def _max_simultaneous(starts, durs, idxs):
    """Most of ``idxs`` sounding at one instant (an end before a start at a tie)."""
    events = sorted(
        (t, d)
        for i in idxs
        if durs[i] > 1e-9
        for t, d in ((starts[i], 1), (starts[i] + durs[i], -1))
    )
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def test_property_at_most_two_talk_parts_sound_at_once_with_overlap_on():
    rnd = random.Random(20261026)
    for _ in range(4000):
        n = rnd.randint(2, 8)
        kinds = rnd.choices(["narration", "clip", "sting"], [6, 2, 1], k=n)
        placements = [
            "under" if k == "clip" and rnd.random() < 0.3 else "sequential"
            for k in kinds
        ]
        speakers = [
            rnd.choice(["a", "b", "c", ""]) if k == "narration" else None for k in kinds
        ]
        durs = [
            rnd.choice([0.0, 0.01, 0.05, 0.15, 0.3, 1.0, 2.0, rnd.uniform(0, 3)])
            for _ in kinds
        ]
        ov = TurnOverlap(
            max_s=rnd.choice([0.1, 0.2, 0.4]),
            jitter=rnd.choice([0.0, 0.3, 1.0]),
            duck_db=-6.0,
            seed=rnd.randint(0, 9),
        )
        starts = layout_placed(
            kinds,
            durs,
            placements,
            clip_edge_overlap_s=rnd.choice([0.0, 0.5]),
            narration_crossfade_s=rnd.choice([0.0, 0.12, 0.3, 0.5]),
            speakers=speakers,
            overlap=ov,
        )
        talk = [i for i, k in enumerate(kinds) if k == "narration"]
        case = (kinds, placements, speakers, durs, ov, starts)
        assert _max_simultaneous(starts, durs, talk) <= 2, case
        assert min(starts) >= 0.0, case


def test_jitter_is_drawn_per_boundary_not_from_a_shared_stream():
    # Turning boundary 1 into a same-voice one must not change the jitter the
    # later boundaries draw (keyed on seed and part index, not a shared stream).
    ov = TurnOverlap(max_s=0.4, jitter=1.0, seed=5)
    starts = _layout(["a", "b", "c", "d"], overlap=ov)
    edited = _layout(["a", "a", "c", "d"], overlap=ov)

    def gaps(s):
        return [2.0 - (b - a) for a, b in zip(s, s[1:])]

    assert gaps(edited)[0] == pytest.approx(XF)  # the edited boundary
    assert gaps(edited)[1:] == pytest.approx(gaps(starts)[1:])  # the rest: same


def test_clips_and_speakerless_parts_never_interrupt():
    ov = TurnOverlap(max_s=0.4, jitter=0.0)
    starts = _layout(["a", None, "b"], overlap=ov)
    assert starts == pytest.approx([0.0, 2.0 - XF, 4.0 - 2 * XF])
    clip = _layout(["a", "b"], overlap=ov, kinds=["narration", "clip"])
    assert clip[1] == pytest.approx(2.0 - 0.5)  # the clip edge rule, untouched


def test_a_clip_with_a_speaker_is_never_an_interruption_nor_ducked():
    ov = TurnOverlap(max_s=0.4, jitter=0.0, duck_db=-6.0)
    kinds, placements = ["narration", "clip", "narration"], ["sequential"] * 3
    speakers = ["a", "b", "c"]  # a stray speaker on the clip must not matter
    assert interrupting_boundaries(kinds, placements, speakers, ov) == []
    starts = _layout(speakers, overlap=ov, kinds=kinds)
    assert duck_regions(starts, [2.0] * 3, kinds, placements, speakers, ov) == {}


def test_duck_regions_are_the_talked_over_tail_of_the_outgoing_voice():
    ov = TurnOverlap(max_s=0.3, jitter=0.0, duck_db=-6.0)
    speakers = ["a", "b", "b"]
    starts = _layout(speakers, overlap=ov)
    kinds, placements = ["narration"] * 3, ["sequential"] * 3
    regions = duck_regions(starts, [2.0] * 3, kinds, placements, speakers, ov)
    assert list(regions) == [0]  # only the change of speaker
    lo, hi = regions[0]
    assert (lo, hi) == (pytest.approx(1.7), 2.0)
    off = TurnOverlap(max_s=0.3, jitter=0.0, duck_db=0.0)
    assert duck_regions(starts, [2.0] * 3, kinds, placements, speakers, off) == {}


def test_config_validates_the_intelligible_range():
    WeaveConfig(overlap_turn_s=MAX_OVERLAP_TURN_S)
    for bad, msg in (
        ({"overlap_turn_s": MAX_OVERLAP_TURN_S + 0.01}, "overlap_turn_s"),
        ({"overlap_turn_s": -0.1}, "overlap_turn_s"),
        ({"overlap_jitter": 1.5}, "overlap_jitter"),
        ({"overlap_duck_db": 3.0}, "overlap_duck_db"),
    ):
        with pytest.raises(ValueError, match=msg):
            WeaveConfig(**bad)


def test_turn_overlap_validates_itself():
    # the rule lives once, on the value the layout consumes
    for bad, msg in (
        ({"max_s": MAX_OVERLAP_TURN_S + 0.01}, "max_s"),
        ({"max_s": -0.1}, "max_s"),
        ({"jitter": -0.1}, "jitter"),
        ({"jitter": 1.5}, "jitter"),
        ({"duck_db": 3.0}, "duck_db"),
    ):
        with pytest.raises(ValueError, match=msg):
            TurnOverlap(**bad)


def test_the_weave_does_not_import_the_tts_layers():
    # braidio.weave is the pure mix engine: importing weave_config (and through
    # it multivoice/tts/mixing) at its top level would invert the layering.
    tree = ast.parse(Path(braidio.weave.__file__).read_text())
    top = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert not [m for m in top if m and m.split(".")[0] in ("braidio", "mixing")]
    cfg = WeaveConfig(overlap_turn_s=0.2, overlap_jitter=0.1, voice_seed=9)
    assert cfg.turn_overlap() == TurnOverlap(max_s=0.2, jitter=0.1, duck_db=0.0, seed=9)


@needs_ffmpeg
def test_render_production_interrupts_on_a_voice_change(monkeypatch, tmp_path):
    import braidio.render as render_mod
    from braidio.weave import duration_s

    monkeypatch.setattr(
        render_mod,
        "narrate",
        lambda text, out_path, **kw: tone(Path(out_path), 440, 1.0),
    )
    script = braidio.Script(
        "t",
        "ovl",
        [
            braidio.Narration("One.", voice="va"),
            braidio.Narration("Two.", voice="vb"),
            braidio.Narration("Three.", voice="vb"),
        ],
    )

    def render(config, name):
        return braidio.render_production(
            script,
            source=None,
            config=config,
            out_path=tmp_path / f"{name}.mp3",
            normalize=False,
            end_fade_s=0.0,
            end_silence_s=0.0,
            return_timeline=True,
            tts_dir=tmp_path / f"tts-{name}",
            clips_dir=tmp_path / "clips",
        )

    base_out, base = render(WeaveConfig(), "base")
    out, tl = render(
        WeaveConfig(overlap_turn_s=0.3, overlap_jitter=0.0, overlap_duck_db=-6.0),
        "ovl",
    )
    base_starts = [s.start for s in base.beats]
    starts = [s.start for s in tl.beats]
    # mp3 encoders pad the tone differently per machine: use the measured lengths
    d = [b.duration for b in tl.beats]
    assert starts[1] == pytest.approx(starts[0] + d[0] - 0.3, abs=1e-3)  # a → b
    assert starts[2] - starts[1] == pytest.approx(d[1] - 0.12, abs=1e-3)  # b → b
    assert starts[1] < base_starts[1]
    # the mixed file is as long as the layout says (tolerance: encoder padding)
    assert duration_s(out) == pytest.approx(starts[2] + d[2], abs=0.15)


@needs_ffmpeg
def test_the_duck_lowers_the_outgoing_tail_in_the_rendered_audio(tmp_path):
    # a, a, b: the outgoing voice (part 1) starts late, so a duck applied after
    # its adelay (on the wrong clock) would miss the tail and this would fail.
    # The incoming part is silence, so the tail window holds only the duck.
    t0 = tone(tmp_path / "t0.wav", 440, 2.0)
    t1 = tone(tmp_path / "t1.wav", 440, 2.0)
    quiet = silence(tmp_path / "s.wav", 2.0)
    items = [
        TimelineItem("narration", str(t0), speaker="a"),
        TimelineItem("narration", str(t1), speaker="a"),
        TimelineItem("narration", str(quiet), speaker="b"),
    ]
    start1 = 2.0 - XF
    start2 = start1 + 2.0 - 0.3  # the tail of part 1 is [start2, start1 + 2.0]

    def tail_over_body(duck_db):
        out = weave_timeline(
            items,
            tmp_path / f"duck{duck_db}.wav",
            narration_crossfade_s=XF,
            overlap=TurnOverlap(max_s=0.3, jitter=0.0, duck_db=duck_db),
        )
        pcm = decode(out)
        tail = rms(pcm, start2 + 0.05, start1 + 2.0 - 0.05)
        body = rms(pcm, start1 + 0.6, start1 + 1.4)
        return tail / body

    assert tail_over_body(0.0) > 0.7  # undocked: the tail is as loud as the body
    assert tail_over_body(-20.0) < 0.3  # -20 dB ≈ 0.1 in amplitude


@needs_ffmpeg
@pytest.mark.skipif(not braidio.HAS_NW, reason="nw (and lacing) not available")
def test_the_graph_path_interrupts_like_the_fast_path(monkeypatch, tmp_path):
    """``overlap_turn_s`` reaches the nw episode: a voice change starts early."""
    from braidio.weave import duration_s

    monkeypatch.setattr(
        braidio,
        "narrate",
        lambda text, out, *, return_cache_status=False, **kw: (
            (tone(Path(out), 440, 1.0), False)
            if return_cache_status
            else tone(Path(out), 440, 1.0)
        ),
    )
    script = braidio.Script(
        "t", "ovl", [braidio.Narration(t) for t in ("One.", "Two.", "Three.")]
    )

    def weave(config, name):
        project = braidio.Project.init(tmp_path / name, title=name)
        ep = braidio.weave_project(project, script, config=config)
        return ep, braidio.transforms._episode.episode_timeline(project, ep)

    pool = dict(voices=("va", "vb"), voice_seed=0)
    base_ep, base = weave(WeaveConfig(**pool), "base")
    ep, tl = weave(WeaveConfig(**pool, overlap_turn_s=0.3, overlap_jitter=0.0), "ovl")
    starts, d = [s.start for s in tl.beats], [s.duration for s in tl.beats]
    # voices alternate va, vb, va: every boundary is a change of speaker
    assert starts[1] == pytest.approx(starts[0] + d[0] - 0.3, abs=1e-3)
    assert starts[2] == pytest.approx(starts[1] + d[1] - 0.3, abs=1e-3)
    assert starts[1] < base.beats[1].start

    # the mixer was handed the same overlap: two 0.3s interruptions shorter
    # than the same script woven without (the encoder's padding cancels out)
    def out_len(episode):
        return duration_s(braidio.transforms._common.url_to_path(episode.body["url"]))

    assert out_len(base_ep) - out_len(ep) == pytest.approx(0.6 - 2 * 0.12, abs=0.1)
