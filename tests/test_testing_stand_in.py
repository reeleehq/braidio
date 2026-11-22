"""braidio.testing.timed_dialogue_stand_in drives the real take/master code offline (#229)."""

import pytest
from _audio import needs_ffmpeg

from braidio import ConversationCast, render_dialogue_lines
from braidio.testing import timed_dialogue_stand_in

CAST = ConversationCast(roles={"A": "voiceA", "B": "voiceB"})
TURNS = [("A", "So the bass line is the hook."), ("B", "Wait, really?")]


@needs_ffmpeg
def test_stand_in_times_each_line_and_records_calls(tmp_path, monkeypatch):
    fake, calls = timed_dialogue_stand_in()
    monkeypatch.setattr("braidio.dialogue_lines.text_to_dialogue_with_timestamps", fake)
    take = render_dialogue_lines(
        TURNS, CAST, out_path=tmp_path / "take.wav",
        output_format="wav_44100", cache=tmp_path / "cache",
    )
    assert [(ln.role, ln.text) for ln in take.lines] == TURNS
    assert len(calls) == 1 and calls[0]["inputs"] == [
        ("voiceA", TURNS[0][1]), ("voiceB", TURNS[1][1])
    ]
    assert take.lines[0].duration_s == pytest.approx(len(TURNS[0][1]) * 0.01, abs=0.02)
