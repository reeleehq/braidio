

def test_respelled_narration_shows_the_written_form_in_captions():
    """braidio#237: the voice gets the respelling, the viewer reads what was written."""
    from braidio.captions import cues_for
    from braidio.markdown_script import script_from_markdown
    from braidio.timeline import BeatSpan, TimelineBreakdown, TurnSpan

    script = script_from_markdown("We ship BDUF today.", respell={"BDUF": "bee-duff"})
    beat = script.beats[0]
    assert "bee-duff" in beat.text and beat.written == "We ship BDUF today."
    for turns in ((), (TurnSpan("We ship bee-duff today.", 0.0, 2.0),)):
        span = BeatSpan(index=0, kind="narration", duration=2.0, start=0.0, turns=turns)
        cues = cues_for(script, TimelineBreakdown(beats=(span,)))
        assert "BDUF" in " ".join(c.text for c in cues)
        assert "bee-duff" not in " ".join(c.text for c in cues)
