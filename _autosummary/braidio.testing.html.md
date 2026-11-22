# braidio.testing

Test helpers for hosts that build on braidio, importable without pytest (braidio#229).

[`timed_dialogue_stand_in()`](#braidio.testing.timed_dialogue_stand_in) replaces the one paid call under
`braidio.render_dialogue_lines()` and `braidio.rerender_line()`, so a host
can test its own code offline with braidio’s real take, master and splice running
(ffmpeg is still needed for those):

```default
fake, calls = braidio.testing.timed_dialogue_stand_in()
monkeypatch.setattr("braidio.dialogue_lines.text_to_dialogue_with_timestamps", fake)
```

### Module Attributes

| [`SECONDS_PER_CHAR`](#braidio.testing.SECONDS_PER_CHAR)   | Seconds of speech per character, the pause around and between turns, the sample rate, the tone amplitude, and the base pitch and pitch step per voice.   |
|---------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`timed_dialogue_stand_in`](#braidio.testing.timed_dialogue_stand_in)()   | A stand-in for braidio's ONE paid dialogue call: `(fake, calls)`.   |
|------------------------------------------------------------------------------|---------------------------------------------------------------------|

### braidio.testing.SECONDS_PER_CHAR *= 0.01*

Seconds of speech per character, the pause around and between turns, the
sample rate, the tone amplitude, and the base pitch and pitch step per voice.

### braidio.testing.timed_dialogue_stand_in()

A stand-in for braidio’s ONE paid dialogue call: `(fake, calls)`.

`fake` has the signature of [`braidio.tts.text_to_dialogue_with_timestamps()`](braidio.tts.html.md#braidio.tts.text_to_dialogue_with_timestamps)
and answers with a sine tone per input (one pitch per voice, 10 ms per
character, 0.2 s pauses) as WAV, plus the
[`DialogueSegment`](braidio.tts.html.md#braidio.tts.DialogueSegment)s that time each input. Each call is
appended to `calls` as `{"inputs": [(voice_id, text), ...], "refresh": bool}`.

```pycon
>>> fake, calls = timed_dialogue_stand_in()
>>> audio, segments = fake([("v", "hello")])
>>> [(s.input_index, s.voice_id) for s in segments], calls[0]["inputs"]
([(0, 'v')], [('v', 'hello')])
```
