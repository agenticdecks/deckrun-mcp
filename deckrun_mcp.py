#!/usr/bin/env python3
"""
Deckrun MCP server — stdio transport.

Behaviour is determined entirely by environment variables:

  No DECKRUN_API_KEY set  →  free tier
    Base URL : https://free.agenticdecks.com
    Tools    : get_slide_format, generate_slide_deck
    Auth     : none

  DECKRUN_API_KEY=dk_live_...  →  paid tier
    Base URL : https://api.agenticdecks.com  (override with DECKRUN_BASE_URL)
    Tools    : get_slide_format, generate_slide_deck,
               generate_video, generate_audio, check_job, get_account
    Auth     : Bearer <key> on every request

Requirements:
    pip install mcp requests

Setup — Claude Desktop (free, no key needed):
    {
      "mcpServers": {
        "deckrun": {
          "command": "python",
          "args": ["/path/to/deckrun_mcp.py"]
        }
      }
    }

Setup — Claude Desktop (paid):
    {
      "mcpServers": {
        "deckrun": {
          "command": "python",
          "args": ["/path/to/deckrun_mcp.py"],
          "env": { "DECKRUN_API_KEY": "dk_live_..." }
        }
      }
    }
"""

import asyncio
import json
import os

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# ---------------------------------------------------------------------------
# Configuration — all behaviour driven by env vars
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("DECKRUN_API_KEY", "").strip()
PAID = bool(_API_KEY)

if PAID:
    _BASE = os.environ.get("DECKRUN_BASE_URL", "https://api.agenticdecks.com").rstrip("/")
    _GENERATE_URL = f"{_BASE}/generate"
else:
    _BASE = os.environ.get("DECKRUN_BASE_URL", "https://free.agenticdecks.com").rstrip("/")
    _GENERATE_URL = f"{_BASE}/free/generate"

SCHEMA_URL = "https://agenticdecks.com/schemas/v1/deckrun-slide-format.json"
SCHEMA_VERSION = "deckrun.v1"
SERVER_NAME = "deckrun" if PAID else "deckrun-free"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOL_GET_SLIDE_FORMAT = types.Tool(
    name="get_slide_format",
    description=(
        "Fetch the authoritative Deckrun slide format specification. "
        "Call this first to learn all layout tags, Markdown syntax, "
        "and rules before writing slides. Returns JSON with layout_tags, "
        "surface_syntax, example_markdown, and limits."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

_TOOL_GENERATE_SLIDE_DECK = types.Tool(
    name="generate_slide_deck",
    description=(
        "Convert Deckrun Markdown into a PDF slide deck. "
        "Call get_slide_format first to learn the correct Markdown format. "
        "Returns: url (public PDF, 90-day expiry), slug, slides (count), "
        "warnings (non-fatal notices to self-correct), schema_version. "
        "Limits: max 10 slides, 50 KB Markdown. "
        "Slides separated by --- on its own line."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": (
                    "Complete slide deck in Deckrun Markdown format. "
                    "Must start with a title slide using <!-- <title-slide /> -->."
                ),
            }
        },
        "required": ["markdown"],
    },
)

_TOOL_GENERATE_VIDEO = types.Tool(
    name="generate_video",
    description=(
        "Convert Deckrun Markdown into a narrated MP4 video (slides + audio). "
        "Always async — returns a job_id. Call check_job to poll until complete. "
        "Requires a paid subscription with the video add-on."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "Complete slide deck in Deckrun Markdown format.",
            },
            "voice": {
                "type": "string",
                "description": "TTS voice ID (optional). Defaults to account default.",
            },
        },
        "required": ["markdown"],
    },
)

_TOOL_GENERATE_AUDIO = types.Tool(
    name="generate_audio",
    description=(
        "Generate an MP3 narration from Deckrun slide notes. "
        "Always async — returns a job_id. Call check_job to poll until complete. "
        "Requires a paid subscription."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "Complete slide deck in Deckrun Markdown format. Notes are used as narration script.",
            },
            "voice": {
                "type": "string",
                "description": "TTS voice ID (optional).",
            },
        },
        "required": ["markdown"],
    },
)

_TOOL_CHECK_JOB = types.Tool(
    name="check_job",
    description=(
        "Poll the status of an async generation job. "
        "Returns: status (queued|running|complete|failed), progress (0–100), "
        "and artifact URLs when complete. "
        "Call repeatedly until status is 'complete' or 'failed'."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Job ID returned by generate_video or generate_audio.",
            }
        },
        "required": ["job_id"],
    },
)

_TOOL_GET_ACCOUNT = types.Tool(
    name="get_account",
    description=(
        "Fetch the current account's plan and usage. "
        "Returns: plan name, render_units_used, render_units_remaining, "
        "active add-ons. Use before large jobs to check quota."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

app = Server(SERVER_NAME)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = [_TOOL_GET_SLIDE_FORMAT, _TOOL_GENERATE_SLIDE_DECK]
    if PAID:
        tools += [_TOOL_GENERATE_VIDEO, _TOOL_GENERATE_AUDIO, _TOOL_CHECK_JOB, _TOOL_GET_ACCOUNT]
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "get_slide_format":
        return await _get_slide_format()
    if name == "generate_slide_deck":
        return await _generate_slide_deck(arguments.get("markdown", ""))
    if PAID:
        if name == "generate_video":
            return await _generate_async(arguments.get("markdown", ""), "mp4", arguments.get("voice"))
        if name == "generate_audio":
            return await _generate_async(arguments.get("markdown", ""), "mp3", arguments.get("voice"))
        if name == "check_job":
            return await _check_job(arguments.get("job_id", ""))
        if name == "get_account":
            return await _get_account()
    return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


# ---------------------------------------------------------------------------
# Tool implementations — shared
# ---------------------------------------------------------------------------

async def _get_slide_format() -> list[types.TextContent]:
    try:
        resp = requests.get(SCHEMA_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        summary = {
            "slide_separator": data.get("surface_syntax", {}).get("slide_separator", "---"),
            "layout_tags": data.get("surface_syntax", {}).get("layout_tags", []),
            "two_column": data.get("surface_syntax", {}).get("two_column", {}),
            "notes": data.get("surface_syntax", {}).get("notes", ""),
            "example_markdown": data.get("example_markdown", ""),
            "limits": {"max_slides": 10, "max_body_size_kb": 50, "pdf_expiry_days": 90},
            "heading_convention": (
                "Title slide uses # (H1) for presentation title. "
                "All other slides use ## (H2) for slide heading."
            ),
            "schema_version": SCHEMA_VERSION,
        }
        return [types.TextContent(type="text", text=json.dumps(summary, indent=2))]
    except Exception as exc:
        fallback = {
            "error": f"Could not fetch live schema ({exc}). Using cached rules.",
            "slide_separator": "---",
            "layout_tags": [
                "<!-- <title-slide /> --> — first slide: title + subtitle",
                "<!-- <title-content-slide /> --> — heading + bullets",
                "<!-- <section-header-slide /> --> — section divider",
                "<!-- <two-content-slide /> --> — two-column",
                "<!-- <title-only-slide /> --> — heading only",
                "<!-- <title-no-footer-slide /> --> — no footer bar",
                "<!-- <content-with-caption-slide /> --> — image with caption",
                "<!-- <full-blank-slide /> --> — fully blank",
                "<!-- <blank-slide /> --> — blank with chrome",
                "<!-- <footer-only-slide /> --> — footer bar only",
            ],
            "heading_convention": "# (H1) for title slide, ## (H2) for all other slides.",
            "limits": {"max_slides": 10, "max_body_size_kb": 50},
            "schema_version": SCHEMA_VERSION,
        }
        return [types.TextContent(type="text", text=json.dumps(fallback, indent=2))]


async def _generate_slide_deck(markdown: str) -> list[types.TextContent]:
    if not markdown.strip():
        return [types.TextContent(type="text", text='{"error": "markdown is empty"}')]
    payload: dict = {"markdown": markdown, "schema_version": SCHEMA_VERSION}
    if PAID:
        payload["output_types"] = ["pdf"]
    try:
        resp = requests.post(_GENERATE_URL, json=payload, headers=_headers(), timeout=120)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    if resp.status_code == 413:
        return [types.TextContent(type="text", text=json.dumps({
            "error": "INPUT_TOO_LARGE",
            "detail": "More than 10 slides or Markdown exceeds 50 KB. Reduce slides and retry.",
        }))]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}",
        "detail": resp.text[:300],
    }))]


# ---------------------------------------------------------------------------
# Tool implementations — paid only
# ---------------------------------------------------------------------------

async def _generate_async(markdown: str, output_type: str, voice: str | None) -> list[types.TextContent]:
    if not markdown.strip():
        return [types.TextContent(type="text", text='{"error": "markdown is empty"}')]
    payload: dict = {
        "markdown": markdown,
        "schema_version": SCHEMA_VERSION,
        "output_types": [output_type],
    }
    if voice:
        payload["voice"] = voice
    try:
        resp = requests.post(_GENERATE_URL, json=payload, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code in (200, 202):
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}",
        "detail": resp.text[:300],
    }))]


async def _check_job(job_id: str) -> list[types.TextContent]:
    if not job_id.strip():
        return [types.TextContent(type="text", text='{"error": "job_id is empty"}')]
    url = f"{_BASE}/jobs/{job_id}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}",
        "detail": resp.text[:300],
    }))]


async def _get_account() -> list[types.TextContent]:
    url = f"{_BASE}/api/profile/me"
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}",
        "detail": resp.text[:300],
    }))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
