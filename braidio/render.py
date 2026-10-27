"""Render a :class:`~braidio.script.Script` into an audio file.

Walks the beats (filtered by the rights :class:`~braidio.rights.Profile`):
narration beats are synthesized (:mod:`braidio.tts`) — as one call, or as paced
turns when the :class:`~braidio.weave_config.WeaveConfig` asks for a smaller
``segmentation_unit`` (:mod:`braidio.pacing`); segment beats are resolved
via a :class:`~braidio.sources.SegmentSource` and extracted (padded + faded);
scene breaks become a sting or a pause (:mod:`braidio.structure`). Every spoken
or clipped part is loudness-normalized, then either woven on a timeline (clips
tuck under narration — :func:`braidio.weave.weave_timeline`) when a
:class:`~braidio.weave_config.WeaveConfig` enables it, or concatenated.

This is the no-graph fast path; the same core is reused by the nw-app
transforms (which add provenance + partial re-render).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mixing import concatenate_audio

from braidio.conversation import (
    DEFAULT_CAST,
    ConversationCast,
    render_dialogue,
    render_turns_sequential,
)
from braidio.defaults import default_delivery
from braidio.delivery import Delivery
from braidio.pacing import NarrationTurn, plan_turns
from braidio.rights import (
    DEFAULT_PROFILE,
    PUBLISHABLE_CLIP_RIGHTS,
    Profile,
    RightsPolicy,
    plan_production,
)
from braidio.leveling import DEFAULT_TARGET_LUFS, level_part
from braidio.script import Script
from braidio.sources import SegmentSource
from braidio.structure import DEFAULT_STRUCTURE, prepare_pause, prepare_sting
from braidio.tts import Speak, narrate, narrator_from_speak
from braidio.weave import TimelineItem, extract_padded, weave_timeline
from braidio.weave_config import WeaveConfig, speaker_key


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg).")


#: Re-normalising the stream before the encoder. ``adelay`` and ``apad`` hand
#: libmp3lame frames whose layout it rejects on some builds — ffmpeg 9.x fails a
#: bare ``adelay`` with "inadequate AVFrame plane padding" / "Error submitting
#: audio frame to the encoder" on perfectly valid input, and the failure is
#: per-file, so a render dies two thirds of the way through. ``aresample``
#: rebuilds the frames and costs nothing audible.
_RESAMPLE = "aresample=44100"


def _lead_gap(src: Path, dst: Path, *, gap_s: float) -> Path:
    """Prepend ``gap_s`` seconds of silence (breathing room before a beat)."""
    _require_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    ms = int(round(gap_s * 1000))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-af",
            f"adelay={ms}:all=1,{_RESAMPLE}",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )
    return dst


def _tail_gap(src: Path, dst: Path, *, gap_s: float) -> Path:
    """Append ``gap_s`` seconds of silence (the pause *after* a spoken turn)."""
    _require_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-af",
            f"apad=pad_dur={gap_s:.3f},{_RESAMPLE}",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )
    return dst


def plan_beat_turns(
    text: str,
    *,
    config: WeaveConfig | None,
    supports_speed: bool,
    settings: dict,
    seed_offset: int,
) -> list[NarrationTurn]:
    """Plan one narration beat's turns, or ``[]`` for the unpaced single call.

    The speed each turn is jittered around is the beat's own ``speed`` setting
    when it sets one (so a grave ``v2-narrator``'s 0.94 survives being paced),
    falling back to ``WeaveConfig.speed_base``. A model with no speed control
    (eleven v3 — :attr:`braidio.delivery.Delivery.supports_speed`,
    :func:`braidio.delivery.model_supports_speed`) plans none.

    Shared by the fast path and the graph's ``narration_render.tts``, which is
    what keeps their per-beat durations (and so their timelines) equal.
    """
    if config is None or not config.paces_narration:
        return []
    speed_base = settings.get("speed", config.speed_base) if supports_speed else None
    return plan_turns(
        text,
        unit=config.segmentation_unit,
        min_turn=config.min_turn,
        max_turn=config.max_turn,
        seed=config.voice_seed + seed_offset,
        gap_s=config.gap_turn_s,
        speed_base=speed_base,
        speed_jitter=config.speed_jitter,
    )


def narrate_turns(
    turns: list[NarrationTurn],
    *,
    stem: Path,
    api_key: str | None,
    voice_id: str | None,
    model_id: str,
    voice_settings: dict,
    narrate_fn=None,
) -> Path:
    """Synthesize a paced narration beat: one TTS call per turn, real silence between.

    This is the point of :mod:`braidio.pacing` — within one beat the tempo now
    varies (a fresh prosodic arc per turn, a per-turn speed where the model has
    one) and the silence between turns is proportional to how strongly the
    boundary closes, instead of one flat arc with nothing inside it.

    ``narrate_fn`` is the synthesis call (default: this module's ``narrate``);
    a caller that wants the cache status, or a stub, passes its own.
    """
    synthesize = narrate_fn or narrate
    parts: list[Path] = []
    for j, turn in enumerate(turns):
        settings = dict(voice_settings)
        if turn.speed is not None:
            settings["speed"] = turn.speed
        part = synthesize(
            turn.text,
            stem.with_name(f"{stem.name}-turn{j:02d}.mp3"),
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            voice_settings=settings,
        )
        if turn.gap_after_s > 0:
            part = _tail_gap(
                part,
                stem.with_name(f"{stem.name}-turn{j:02d}-gap.mp3"),
                gap_s=turn.gap_after_s,
            )
        parts.append(part)
    if len(parts) == 1:
        return parts[0]
    out = stem.with_name(f"{stem.name}-paced.mp3")
    # crossfade=0: the planned gaps ARE the spacing; a crossfade would eat them.
    concatenate_audio(*[str(p) for p in parts], output=str(out), crossfade=0.0)
    return out


def _end_tail(path: Path, *, fade_s: float, silence_s: float) -> None:
    """Soften the ending: a short fade on the last ``fade_s`` + ``silence_s`` of
    trailing silence, so the production *lands* instead of cutting dead.

    Cosmetic post-step: if the output can't be probed/re-encoded (e.g. a mocked
    file in tests), it's skipped rather than failing the whole render.
    """
    _require_ffmpeg()
    from braidio.weave import duration_s

    try:
        dur = duration_s(path)
        fo_start = max(0.0, dur - fade_s)
        tmp = path.with_name(path.stem + "-tail.mp3")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-af",
                f"afade=t=out:st={fo_start:.3f}:d={fade_s},apad=pad_dur={silence_s}",
                str(tmp),
            ],
            check=True,
            capture_output=True,
        )
        tmp.replace(path)
    except (subprocess.CalledProcessError, ValueError, KeyError):
        pass  # not a real/probeable audio file — leave it untailed


def _tts_name(speak: Speak | None) -> str:
    """The voice backend a render used, as its settings record names it."""
    if speak is None:
        from braidio.timeline import DEFAULT_TTS

        return DEFAULT_TTS
    name = getattr(speak, "__qualname__", None) or type(speak).__qualname__
    return f"{getattr(speak, '__module__', '') or ''}:{name}".lstrip(":")


def render_production(
    script: Script,
    *,
    source: SegmentSource,
    api_key: str | None = None,
    speak: Speak | None = None,
    config: WeaveConfig | None = None,
    profile: Profile = DEFAULT_PROFILE,
    rights: RightsPolicy | None = None,
    delivery: Delivery | str | None = None,
    cast: ConversationCast = DEFAULT_CAST,
    out_path: str | Path | None = None,
    voice_id: str | None = None,
    crossfade_s: float = 0.12,
    normalize: bool = True,
    music_bed=None,  # optional braidio.music.MusicBed — instrumental underscore
    structure=None,  # optional braidio.structure.MusicStructure — stings + spotlight
    end_fade_s: float = 0.35,  # soft landing: fade the last bit + trailing silence
    end_silence_s: float = 0.7,
    return_timeline: bool = False,  # also return a TimelineBreakdown of the render
    tts_dir: str | Path = "data/tts",
    clips_dir: str | Path = "data/clips",
    episodes_dir: str | Path = "data/episodes",
) -> "Path | tuple[Path, object]":
    """Render ``script`` under ``profile`` → a single audio file. Returns the path.

    With ``return_timeline=True`` returns ``(path, TimelineBreakdown)`` instead —
    the render records what it spent time on (per-beat kind, source interval,
    duration, and offset) rather than leaving it to be reconstructed afterward,
    plus, in ``TimelineBreakdown.settings``, the settings that produced those
    timings (see :func:`braidio.timeline.render_settings`). Recording them is
    what keeps a render reproducible: the pacing knobs below mean one script has
    many possible cuts, so a persisted breakdown has to say which one it is.

    Segment beats are resolved through ``source`` (a :class:`SegmentSource`).
    When ``config`` has ``clip_edge_overlap_s > 0``, a clip is ``placement="under"``,
    or a ``music_bed`` is given, the parts are woven on a timeline; otherwise they
    are concatenated. ``rights`` (if given) sets which segment rights are
    publishable. ``music_bed`` lays an instrumental underscore under the whole
    production (see :class:`braidio.music.MusicBed`).

    ``structure`` (a :class:`braidio.structure.MusicStructure`) is how the
    production marks its structure with music: a scene-break beat plays its
    ``sting`` (or a pause when there is none / the break is marked ``"none"``),
    and a spotlit segment beat drops the bed out for its duration. ``None``
    uses the inert defaults — no sting asset, no clip spotlit by default — so a
    script without scene breaks or spotlight flags renders exactly as before.

    **Pacing inside a narration beat** is a ``config`` choice. By default
    (``WeaveConfig.segmentation_unit == "beat"``) a beat is one TTS call — one
    prosodic arc, no silence anywhere in it. Set a smaller
    ``segmentation_unit`` and the beat is cut into turns of
    ``min_turn..max_turn`` units, each synthesized separately with a jittered
    ``speed_base ± speed_jitter`` (where the model has a speed knob) and
    followed by ``gap_turn_s`` scaled to how strongly the boundary closes —
    see :mod:`braidio.pacing`. Those four knobs do **nothing** here at the
    default unit, which is the historical behavior kept byte-identical.

    ``api_key`` is an optional per-request ElevenLabs key threaded to every
    synthesized beat — both narration (:func:`braidio.tts.narrate`) and dialogue
    (:func:`braidio.conversation.render_dialogue`). When ``None`` (default) each
    synthesizer falls back to ``$ELEVENLABS_API_KEY`` (unchanged behavior); an
    explicit key lets a caller (e.g. a per-user BYO-key request) override the
    environment without mutating it. Segment beats never call ElevenLabs, so the
    key does not touch them.

    ``speak`` swaps the voice backend (a :data:`braidio.tts.Speak`, e.g. a local
    model through ``voxy``): every narration beat and turn is spoken through it,
    and a dialogue beat is spoken turn by turn (only ElevenLabs has the one-pass
    Text-to-Dialogue path). Formats, pacing and weaving are unchanged. It is
    handed the voice ids this render resolves (the format's, the cast's), so
    pass ``voice_id=`` / ``cast=`` in its vocabulary, or map them inside it.
    ``None`` (default) is the ElevenLabs path, exactly as before.
    """
    # the voice backend: ElevenLabs unless the caller supplied another one
    synthesize = narrate if speak is None else narrator_from_speak(speak)
    publishable = rights.publishable_clip_rights if rights else PUBLISHABLE_CLIP_RIGHTS
    plan = plan_production(script, profile, publishable_clip_rights=publishable)
    target_lufs = config.target_lufs if config is not None else DEFAULT_TARGET_LUFS
    duck_db = config.duck_db if config is not None else -15.0
    structure = structure if structure is not None else DEFAULT_STRUCTURE

    out = (
        Path(out_path)
        if out_path
        else Path(episodes_dir) / f"{script.id_slug}-{profile.value}.mp3"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tts_dir) / f"_render_{script.id_slug}_{profile.value}"
    work.mkdir(parents=True, exist_ok=True)

    # extraction pads: from config when present, else small defaults
    # `delivery=None` means "whatever this user/machine has settled on":
    # explicit arg > $BRAIDIO_DELIVERY > ~/.config/braidio/config.json > package
    # default (v3-presenter). Resolved here rather than as a signature default so
    # a config edit takes effect without reimporting.
    delivery = default_delivery(delivery)

    if config is not None:
        pre, post, fi, fo = (
            config.clip_pre_roll_s,
            config.clip_post_roll_s,
            config.clip_fade_in_s,
            config.clip_fade_out_s,
        )
        clip_min_len = config.clip_min_len_s
    else:
        pre, post, fi, fo = 0.15, 0.35, 0.04, 0.04
        clip_min_len = 2.2

    parts: list[Path] = []
    kinds: list[str] = []
    placements: list[str] = []  # "sequential" | "under" (per part, for the weave)
    roles: list[
        str
    ] = []  # aggregation label per beat (clip / narration / style / dialogue)
    spans: list[tuple[float, float] | None] = []  # source [start,end) for clips
    labels: list[str] = []
    spotlights: list[bool] = []  # per part: the bed drops out over it
    speakers: list[str | None] = []  # per part: voice id talking (None: clip/sting)
    for i, pb in enumerate(plan.beats):
        orig = script.beats[pb.from_index]
        clip_span: tuple[float, float] | None = None
        if pb.kind == "scene_break":
            # structure: a sting when the production has one and the break asks
            # for it, else a beat of silence. Both are pre-levelled — no loudnorm.
            if structure.plays_sting(orig):
                raw = prepare_sting(
                    structure.sting,
                    work / f"sting{i:02d}.mp3",
                    target_lufs=target_lufs,
                )
            else:
                raw = prepare_pause(structure.pause_s, work / f"pause{i:02d}.mp3")
            parts.append(raw)
            kinds.append("sting")
            placements.append("sequential")
            roles.append("sting" if structure.plays_sting(orig) else "scene-break")
            labels.append(orig.label)
            spans.append(None)
            spotlights.append(False)
            speakers.append(None)
            continue
        if pb.kind == "narration":
            # per-beat override > the render's voice > the config's pool pick
            beat_voice = getattr(orig, "voice", None) or voice_id
            if beat_voice is None and config is not None:
                beat_voice = config.cast_voice(pb.from_index)
            beat_settings = (
                getattr(orig, "voice_settings", None) or delivery.voice_settings
            )
            beat_stem = (
                Path(tts_dir)
                / f"{script.id_slug}-{profile.value}-{delivery.name}-beat{i:02d}"
            )
            # Intra-beat pacing — empty plan (the default `segmentation_unit=
            # "beat"`) keeps the historical one-call-per-beat path untouched.
            turns = plan_beat_turns(
                pb.content,
                config=config,
                supports_speed=delivery.supports_speed,
                settings=beat_settings,
                seed_offset=pb.from_index,  # the script position, as the graph's beat_id
            )
            if turns:
                raw = narrate_turns(
                    turns,
                    stem=beat_stem,
                    api_key=api_key,
                    voice_id=beat_voice,
                    model_id=delivery.model_id,
                    voice_settings=beat_settings,
                    narrate_fn=synthesize,
                )
            else:
                raw = synthesize(
                    pb.content,
                    beat_stem.with_name(beat_stem.name + ".mp3"),
                    api_key=api_key,
                    voice_id=beat_voice,
                    model_id=delivery.model_id,
                    voice_settings=beat_settings,
                )
            # breathing room before a register change (Narration.lead_gap_s)
            gap = getattr(orig, "lead_gap_s", 0.0)
            if gap and gap > 0:
                raw = _lead_gap(
                    raw, Path(tts_dir) / f"{script.id_slug}-lead{i:02d}.mp3", gap_s=gap
                )
        elif pb.kind == "dialogue":
            dlg_path = (
                Path(tts_dir) / f"{script.id_slug}-{profile.value}-dlg{i:02d}.mp3"
            )
            if speak is None:
                raw = render_dialogue(
                    list(pb.turns), cast, api_key=api_key, out_path=dlg_path
                )
            else:
                raw = render_turns_sequential(
                    list(pb.turns),
                    cast,
                    out_path=dlg_path,
                    work_dir=dlg_path.with_suffix(""),
                    narrate_fn=synthesize,
                )
        else:  # segment
            rs = source.resolve(pb.content)
            if rs is None:
                raise LookupError(f"segment did not resolve: {pb.content[:50]!r}")
            clip_span = (float(rs.start_s), float(rs.end_s))
            raw = Path(clips_dir) / f"{script.id_slug}-seg{i:02d}.mp3"
            extract_padded(
                rs.asset_path,
                rs.start_s,
                rs.end_s,
                raw,
                pre_roll_s=pre,
                post_roll_s=post,
                fade_in_s=fi,
                fade_out_s=fo,
                min_len_s=clip_min_len,
            )
        parts.append(
            level_part(raw, work / f"part{i:02d}.mp3", target_lufs=target_lufs)
            if normalize
            else raw
        )
        # a dialogue beat is several speakers in one file: it never interrupts
        speakers.append(speaker_key(beat_voice) if pb.kind == "narration" else None)
        # dialogue + narration are spoken → treated as "narration" on the timeline
        kinds.append("clip" if pb.kind == "clip" else "narration")
        # a "under" clip becomes a ducked underlay; "before"/"after" play clean
        seg_place = getattr(orig, "placement", "before")
        placements.append(
            "under" if (pb.kind == "clip" and seg_place == "under") else "sequential"
        )
        # fade-to-spotlight: a clip's own flag, else the structure's default
        spotlights.append(pb.kind == "clip" and structure.spotlight_for(orig))
        # per-beat timeline metadata (for the returned TimelineBreakdown)
        if pb.kind == "clip":
            roles.append("clip")
            labels.append(getattr(orig, "label", ""))
        elif pb.kind == "dialogue":
            roles.append("dialogue")
            labels.append(getattr(orig, "label", "") or "dialogue")
        else:  # narration — kind sub-labelled by its style (e.g. "book-passage")
            roles.append(getattr(orig, "style", None) or "narration")
            labels.append(
                (getattr(orig, "text", "") or "")[:48]
            )  # text snippet, not the style
        spans.append(clip_span)

    has_under = "under" in placements
    edge_overlap = config.clip_edge_overlap_s if config is not None else 0.0
    crossfade = config.crossfade_s if config is not None else crossfade_s
    turn_overlap = config.turn_overlap() if config is not None else None
    # a music bed also needs the mix path (even for narration-only productions);
    # so does a speaker interruption, which a plain concat cannot place
    woven = (
        music_bed is not None
        or ("clip" in kinds and (edge_overlap > 0 or has_under))
        or (turn_overlap is not None and turn_overlap.max_s > 0)
    )

    if woven:
        items = [
            TimelineItem(
                k,
                str(p),
                placement=pl,
                duck_db=(duck_db if pl == "under" else 0.0),
                spotlight=sp,
                speaker=spk,
            )
            for k, p, pl, sp, spk in zip(kinds, parts, placements, spotlights, speakers)
        ]
        weave_timeline(
            items,
            out,
            clip_edge_overlap_s=edge_overlap,
            narration_crossfade_s=crossfade,
            target_lufs=target_lufs,
            bed=music_bed,
            overlap=turn_overlap,
        )
    else:
        # `crossfade`, not `crossfade_s`: the config's crossfade was read by the
        # woven branch and by the returned TimelineBreakdown, but NOT here — so a
        # narration-only render used the function default while the breakdown
        # reported the config's value. Same value at the shared default (0.12),
        # so this only starts honouring a crossfade a caller explicitly set.
        concatenate_audio(
            *[str(p) for p in parts], output=str(out), crossfade=crossfade
        )
    # soft landing so the production doesn't cut dead on the last word
    if end_silence_s > 0 or end_fade_s > 0:
        _end_tail(out, fade_s=end_fade_s, silence_s=end_silence_s)

    if return_timeline:
        from braidio.timeline import build_timeline, render_settings
        from braidio.weave import duration_s

        durs = [duration_s(p) for p in parts]
        tl = build_timeline(
            kinds=roles,
            durations=durs,
            placements=placements,
            labels=labels,
            source_spans=spans,
            clip_edge_overlap_s=edge_overlap,
            narration_crossfade_s=crossfade,
            speakers=speakers,
            overlap=turn_overlap,
            title=script.title,
            # The settings that produced these timings, not the ones asked for:
            # `crossfade`/`edge_overlap`/`target_lufs`/`duck_db` are the values
            # this render resolved above. Without the record, a re-render months
            # later can silently cut differently and nothing says why (#63 made
            # the pacing knobs real, which is what opened that hole).
            settings=render_settings(
                config=config,
                crossfade_s=crossfade,
                clip_edge_overlap_s=edge_overlap,
                target_lufs=target_lufs,
                duck_db=duck_db,
                delivery=delivery,
                profile=profile,
                normalize=normalize,
                tts=_tts_name(speak),
            ),
        )
        return out, tl
    return out
