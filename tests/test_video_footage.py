"""Footage panels: a panel that plays recorded video as a straight cut.

These render real (tiny) media with ffmpeg, because what matters is what lands
in the file: the right frames from the right in-point, for exactly the panel's
span, with the narration under it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from braidio.video import (
    HAS_VIDEO,
    Footage,
    Panel,
    segment_argv,
    frame_counts,
    render_video,
    runs,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

SIZE = (160, 90)
FPS = 30
#: One colour per second of the source, so a frame says where it was cut from.
COLOURS = ("red", "blue", "green")
RGB = {"red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 128, 0)}


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture
def media(tmp_path):
    """A 3 s source (red, blue, green seconds) and 4 s of narration."""
    parts = []
    for i, colour in enumerate(COLOURS):
        part = tmp_path / f"{i}.mp4"
        _ffmpeg(
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r=30:d=1",
            "-pix_fmt", "yuv420p", str(part),
        )
        parts.append(part)
    listing = tmp_path / "parts.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    source = tmp_path / "source.mp4"
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(source))
    audio = tmp_path / "narration.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", str(audio))
    return source, audio


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,nb_read_frames",
            "-of", "default=nw=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    info = dict(line.split("=", 1) for line in out.split())
    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return {
        "size": (int(info["width"]), int(info["height"])),
        "frames": int(info["nb_read_frames"]),
        "audio": bool(has_audio),
    }


def _colour_at(path: Path, t: float, tmp_path: Path) -> str:
    from PIL import Image

    png = tmp_path / f"frame_{t:.2f}.png"
    _ffmpeg("-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", str(png))
    with Image.open(png) as img:
        px = img.convert("RGB").getpixel((img.width // 2, img.height // 2))
    return min(RGB, key=lambda c: sum((a - b) ** 2 for a, b in zip(RGB[c], px)))


# --- pure -------------------------------------------------------------------


def test_runs_groups_stills_and_isolates_footage():
    f = Footage("v.mp4")
    ps = [Panel(0, 1, "a"), Panel(1, 2, "b", footage=f), Panel(2, 3, "c"), Panel(3, 4, "d")]
    assert runs(ps) == [("stills", [0]), ("footage", [1]), ("stills", [2, 3])]


def test_frame_counts_round_boundaries_not_durations():
    # 35 panels of 1/3 s: rounding each duration alone would drift by frames
    ps = [Panel(i / 3, (i + 1) / 3, "x") for i in range(35)]
    counts = frame_counts(ps, fps=FPS)
    assert sum(counts) == round(35 / 3 * FPS)


def test_a_cut_must_have_frames():
    with pytest.raises(ValueError, match="must have frames"):
        segment_argv("a.mp4", 0.0, 0, out_path="o.mp4")


def test_an_in_point_past_the_footage_is_refused_before_rendering(tmp_path):
    """Seeked past its end, ffmpeg yields no frames: the cut would come out short
    and every later panel would slide off its narration."""
    calls = []
    panels = [
        Panel(0.0, 1.0, "p.png", footage=Footage("rec.mp4", 10.0)),
        Panel(1.0, 2.0, "p.png", footage=Footage("rec.mp4", 65.0)),
    ]
    with pytest.raises(ValueError, match="only 60.00s long"):
        render_video(
            panels, audio_path="a.wav", out_path=tmp_path / "o.mp4",
            runner=calls.append, footage_duration=lambda path: 60.0,
        )
    assert calls == []


def test_a_render_leaves_no_pieces_behind_and_shares_none(tmp_path):
    """Pieces live in a directory private to one render, so two renders into one
    project's workdir cannot pick up each other's, and none is left over."""
    import os

    seen = []

    def runner(argv):
        seen.append(argv[-1])
        Path(argv[-1]).write_bytes(b"x")

    for out in ("a.mp4", "b.mp4"):
        render_video(
            [Panel(0.0, 1.0, "p.png", footage=Footage("rec.mp4", 0.0))],
            audio_path="a.wav", out_path=tmp_path / out, workdir=tmp_path / "work",
            runner=runner, footage_duration=lambda path: 5.0,
        )
    pieces = [s for s in seen if "piece_" in s]
    assert len(pieces) == 2 and os.path.dirname(pieces[0]) != os.path.dirname(pieces[1])
    assert list((tmp_path / "work").iterdir()) == []


def test_a_panel_without_footage_is_a_still_panel():
    assert Panel(0, 1, "a.jpg").footage is None


# --- rendered ---------------------------------------------------------------


@needs_ffmpeg
def test_many_cuts_of_one_recording_stay_on_the_frame(media, tmp_path):
    """60 one-frame-apart cuts: the film is exactly as long as the panels say."""
    source, audio = media
    panels = [
        Panel(i * 0.05, (i + 1) * 0.05, "p.png", footage=Footage(str(source), (i % 30) * 0.1))
        for i in range(60)
    ]
    out = render_video(
        panels, audio_path=audio, out_path=tmp_path / "many.mp4", size=SIZE, fps=FPS,
        workdir=tmp_path / "work",
    )
    assert _probe(out)["frames"] == 90


@needs_ffmpeg
def test_footage_panels_cut_from_their_in_points(media, tmp_path):
    source, audio = media
    panels = [
        # 0.0-1.0 of the film plays 0.2-1.2 of the source: red
        Panel(0.0, 1.0, "poster.png", footage=Footage(str(source), 0.2)),
        # 1.0-2.0 plays from 2.1: green (a jump back and forth is fine)
        Panel(1.0, 2.0, "poster.png", footage=Footage(str(source), 2.1)),
        # 2.0-3.0 plays from 1.1: blue
        Panel(2.0, 3.0, "poster.png", footage=Footage(str(source), 1.1)),
    ]
    out = render_video(
        panels, audio_path=audio, out_path=tmp_path / "cut.mp4", size=SIZE, fps=FPS,
        workdir=tmp_path / "work",
    )
    info = _probe(out)
    assert info == {"size": SIZE, "frames": 90, "audio": True}
    assert [_colour_at(out, t, tmp_path) for t in (0.4, 1.4, 2.4)] == ["red", "green", "blue"]


@needs_ffmpeg
def test_footage_that_runs_out_holds_its_last_frame(media, tmp_path):
    source, audio = media
    # starts 0.5 s before the source ends but must fill 2 s
    panels = [Panel(0.0, 2.0, "poster.png", footage=Footage(str(source), 2.5))]
    out = render_video(
        panels, audio_path=audio, out_path=tmp_path / "held.mp4", size=SIZE, fps=FPS,
        workdir=tmp_path / "work",
    )
    assert _probe(out)["frames"] == 60
    assert _colour_at(out, 1.8, tmp_path) == "green"


@needs_ffmpeg
def test_footage_is_fitted_not_cropped(media, tmp_path):
    source, audio = media
    panels = [Panel(0.0, 1.0, "poster.png", footage=Footage(str(source), 0.0))]
    out = render_video(
        panels, audio_path=audio, out_path=tmp_path / "tall.mp4", size=(90, 160),
        fps=FPS, workdir=tmp_path / "work",
    )
    assert _probe(out)["size"] == (90, 160)
    from PIL import Image

    png = tmp_path / "tall.png"
    _ffmpeg("-ss", "0.5", "-i", str(out), "-frames:v", "1", str(png))
    with Image.open(png) as img:
        rgb = img.convert("RGB")
        top, middle = rgb.getpixel((45, 5)), rgb.getpixel((45, 80))
    assert sum(top) < 40, "a wide source in a tall frame is letterboxed, not cropped"
    assert middle[0] > 200


@needs_ffmpeg
@pytest.mark.skipif(not HAS_VIDEO, reason="braidio[video] (burns, pillow) not installed")
def test_stills_and_footage_mix_in_one_film(media, tmp_path):
    from PIL import Image

    source, audio = media
    still = tmp_path / "still.png"
    Image.new("RGB", (320, 180), (255, 255, 0)).save(still)
    panels = [
        Panel(0.0, 1.0, str(still), style="push", zoom=1.1),
        Panel(1.0, 2.0, "poster.png", footage=Footage(str(source), 1.2)),
        Panel(2.0, 3.0, str(still), style="drift", zoom=1.05),
    ]
    out = render_video(
        panels, audio_path=audio, out_path=tmp_path / "mixed.mp4", size=SIZE, fps=FPS,
        workdir=tmp_path / "work",
    )
    assert _probe(out)["frames"] == 90
    assert _colour_at(out, 1.5, tmp_path) == "blue"


@needs_ffmpeg
def test_full_range_footage_is_delivered_in_tv_range(tmp_path):
    """A screencast's frames are JPEGs, so its video is full-range yuvj420p; the
    delivered film must be the TV-range yuv420p every player shows correctly."""
    src = tmp_path / "screen.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=2",
        "-vf", "scale=out_range=pc,format=yuvj420p", str(src),
    )
    audio = tmp_path / "a.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio))
    out = render_video(
        [Panel(0.0, 1.0, "p.png", footage=Footage(str(src), 0.5))],
        audio_path=audio, out_path=tmp_path / "o.mp4", size=SIZE, fps=FPS,
        workdir=tmp_path / "work",
    )
    fmt = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=pix_fmt,color_range", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    # TV range: the pixels are converted, and the film is not flagged full range
    # (an unflagged stream is limited range to every player)
    pix_fmt, color_range = fmt.split(",")
    assert pix_fmt == "yuv420p" and color_range in ("tv", "unknown")
