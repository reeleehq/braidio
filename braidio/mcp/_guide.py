"""The braidio MCP server's self-description — one SSOT for both the server-level
``instructions`` (shown to the model automatically) and the ``help`` tool (called
when a user asks what braidio can do).

Keep this in step with the tool surface in :mod:`braidio.mcp` — it is the connector's
front door.
"""

from __future__ import annotations

#: Natural-language server instructions (FastMCP ``instructions=``). The model sees
#: this without calling any tool, so lead with what braidio is and the happy path.
INSTRUCTIONS = """\
braidio turns source material into a narrated audio production — it weaves authored
narration (ElevenLabs text-to-speech) together with extracted segments of source
media into one mixed audio file. (Video is on the roadmap.)

To turn a document into audio, the typical path is:
1. create_project(project_id) — a workspace for this production.
2. ingest_document(project_id, uri=...) OR (project_id, text=...) — pull in the source
   (a public URL, a PDF, or pasted text). It returns the extracted plaintext for you
   to read and analyze.
3. Analyze that text and author a Script: an ordered list of beats — "narration" beats
   (spoken commentary you write, in your own words = rights-safe) and, when there is
   source audio to quote, "segment" beats. Break the material into a natural spoken
   sequence.
4. estimate_cost(script, format_id or delivery, profile) — preview the ElevenLabs spend of the render you will run, BEFORE rendering (free).
5. save_script(project_id, script) — link the beats into the project graph (free,
   optional), or go straight to rendering.
6. render_production(script, ...) OR weave_project(project_id, script, ...) — synthesize
   and mix into one audio file. This SPENDS ElevenLabs credits (metered per user).

If the Script has "segment" beats that quote source audio/video, first put the media
in your library: download_audio(url) when you have a song/video *link* (YouTube,
SoundCloud, … — it pulls the audio; respect copyright), or
upload_asset(uri=... or data_b64=...) for a direct file URL or bytes. Either returns
an `itemId`; reference it from the segment source as `source.asset_id` in ANY
render that takes a source — render_production / render_format (one-shot) or
save_script / weave_project (project graph). Manage your library with list_assets /
get_asset.

Guidance: call `help` for the full capability + tool catalog. Always `estimate_cost`
before a render. The tools that spend money are narrate, the render_* tools,
compose_narration, and weave_project; the rest (catalog, planning, ingest, assets,
text-prep) are free. Every call is metered to the authenticated caller. Ask a clarifying question if the desired voice, format, or
length is unclear before spending on a render.\
"""


def capabilities() -> dict:
    """Structured capability guide (returned by the ``help`` tool)."""
    return {
        "what_is_braidio": (
            "A tool for turning source material into narrated audio productions: it "
            "weaves authored narration (ElevenLabs TTS) with extracted segments of "
            "source media into one mixed audio file. Video is roadmap."
        ),
        "workflow": [
            "create_project(project_id) — a workspace for the production.",
            "ingest_document(project_id, uri= or text=) — pull in source material; "
            "returns the extracted text to analyze.",
            "Author a Script from that text: ordered narration + segment beats.",
            "estimate_cost(script, format_id|delivery, profile) — preview that render's ElevenLabs spend (free).",
            "save_script(project_id, script) — link the beats into the graph (free), "
            "or render directly.",
            "render_production(script) or weave_project(project_id, script) — "
            "synthesize + mix to audio (COSTED, metered per user).",
        ],
        "tools": {
            "assistance": ["help"],
            "discover": [
                "list_formats",
                "list_presets",
                "list_deliveries",
                "list_voice_pools",
                "describe_genre",
            ],
            "source_and_authoring_free": [
                "ingest_document",
                "clean_text",
                "find_forbidden_quotes",
                "audit_platitudes",
                "narration_segments",
                "assign_voices",
                "find_segment",
            ],
            "plan_and_estimate_free": [
                "estimate_cost",
                "plan_production",
                "build_timeline",
                "content_violations",
            ],
            "projects_free": [
                "create_project",
                "list_projects",
                "project_status",
                "save_script",
            ],
            "assets_free": [
                "download_audio",
                "upload_asset",
                "list_assets",
                "get_asset",
            ],
            "render_COSTED": [
                "render_production",
                "render_format",
                "weave_project",
                "narrate",
                "render_dialogue",
                "render_multivoice",
                "compose_narration",
            ],
        },
        "script_shape": {
            "title": "string",
            "id_slug": "short stable id, e.g. '01'",
            "beats": [
                {"type": "narration", "text": "spoken commentary you author"},
                {
                    "type": "segment",
                    "reference": "a quote/label to resolve against source audio",
                    "rights": "owned-local | public-domain | copyrighted",
                },
                {"type": "dialogue", "turns": [["A", "line"], ["B", "reply"]]},
                {
                    "type": "scene_break",
                    "label": "a new section starts here (a sting or a pause; free)",
                },
            ],
        },
        "notes": [
            "Costed tools (narrate, render_*, weave_project) spend ElevenLabs "
            "credits — call estimate_cost first.",
            "Segment beats need a `source` (timed lines + audio): upload the media "
            "with upload_asset and pass its itemId as `source.asset_id`, or point "
            "`source.asset_path` at a server-local file. A narration-only script "
            "needs no source.",
            "A scene_break beat marks a section boundary (put one between a "
            "debate's phases or a panel's rounds). Upload a bed/sting instrumental "
            "with upload_asset, then pass its itemId as `bed_asset_id` (continuous "
            "underscore) / `sting_asset_id` (plays at each scene_break) to "
            "render_production, render_format, save_script or weave_project — "
            "without a sting a scene_break is just a pause, and a segment beat's "
            "spotlight flag needs `bed_asset_id` specifically (there's no bed to "
            "drop out).",
            "Formats vary in what they use a bed/sting for: a format with "
            "music_bed='none' (e.g. song_exploder) never renders a bed at all, "
            "so render_format, save_script and weave_project all refuse "
            "`bed_asset_id` there up front rather than charge you (or, for "
            "save_script, ingest it) and drop it. A sting can still "
            "legitimately go unused (e.g. the format's default scene_marker is "
            "'none' and the script never overrides it per scene_break) — the "
            "result of all three carries `sting_applied` / "
            "`sting_ignored_reason` so you know either way "
            "(render_production takes no format, so this doesn't apply there).",
            "A dialogue beat's turn roles must be the cast's role names: the "
            "format's (deep_dive is host_a/host_b, interview is host/guest — see "
            "list_formats) when you pass `format_id`, else the default cast's A/B. "
            "save_script and weave_project check this before writing anything; "
            "recasting (a different format_id) re-renders only the dialogue beats.",
            "`profile` is the rights cut, and it now applies on BOTH render "
            "paths: 'personal' plays every clip, 'published' plays only clips "
            "whose rights allow it (dropping the rest, or swapping in a beat's "
            "published_substitute). Pass it to render_production, render_format, "
            "save_script or weave_project; call plan_production first to see what "
            "a profile would drop, free.",
            "Every call is metered to the authenticated user; the connector is "
            "restricted to its allowlisted users.",
            "Bring your own ElevenLabs key by sending it as the `X-Elevenlabs-Key` "
            "request header: every costed tool (the one-shot renders and "
            "weave_project alike) then bills that key instead of the connector's. "
            "The key is used for the call and never stored — not in your project, "
            "its renders, or the usage ledger.",
        ],
    }
