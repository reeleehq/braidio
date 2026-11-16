"""Cutting a passage out of a real audio file (needs ffmpeg)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from braidio import weave
from braidio.weave import duration_s, extract_padded

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
ROLLS = dict(pre_roll_s=0.15, post_roll_s=0.25, fade_in_s=0.2, fade_out_s=0.3)


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", *map(str, args)], check=True, capture_output=True)


@pytest.fixture
def episode(tmp_path):
    """Thirty seconds of tone as an MP3 that carries a cover picture, as a podcast episode file often does."""
    tone, cover, path = tmp_path / "tone.mp3", tmp_path / "cover.png", tmp_path / "episode.mp3"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=30", tone)
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", cover)
    _ffmpeg("-i", tone, "-i", cover, "-map", "0:a", "-map", "1:v", "-c:a", "copy", "-c:v", "mjpeg",
            "-disposition:v", "attached_pic", "-id3v2_version", "3", path)  # fmt: skip
    return path


def test_a_passage_cut_from_a_file_with_a_cover_picture_is_audio_only_and_has_its_full_length(episode, tmp_path):
    clip = extract_padded(episode, 12.0, 20.0, tmp_path / "clip.mp3", **ROLLS)
    assert duration_s(clip) == pytest.approx(8.4, abs=0.15)
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True,
    ).stdout.split()  # fmt: skip
    assert streams == ["audio"]  # the cover is not carried into the cut (it once ended the cut after 0.24 s)


def test_a_cut_that_comes_out_far_shorter_than_asked_is_an_error_not_a_clip(episode, tmp_path, monkeypatch):
    """ffmpeg can exit 0 with a quarter of a second of output; a production must not lose the passage silently."""
    real_run = subprocess.run

    def cut_short(command, **kwargs):
        if command[0] == "ffmpeg" and "-t" in command:
            command = list(command)
            command[command.index("-t") + 1] = "0.24"
        return real_run(command, **kwargs)

    monkeypatch.setattr(weave.subprocess, "run", cut_short)
    with pytest.raises(RuntimeError, match="came out 0.2"):
        extract_padded(episode, 12.0, 20.0, tmp_path / "clip.mp3", **ROLLS)


def test_a_cut_at_the_very_end_of_a_file_may_be_shorter_than_asked(episode, tmp_path):
    clip = extract_padded(episode, 28.0, 40.0, tmp_path / "clip.mp3", **ROLLS)  # the file ends at 30 s
    assert 1.5 < duration_s(clip) < 3.0
