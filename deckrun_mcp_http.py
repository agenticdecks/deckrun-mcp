#!/usr/bin/env python3
"""
Deckrun MCP server — Streamable HTTP transport (spec 2025-03-26+).

Behaviour is determined entirely by environment variables:

  No DECKRUN_API_KEY set  →  free tier (2 tools, no auth forwarded)
  DECKRUN_API_KEY=dk_live_...  →  paid tier (6 tools, auth forwarded)

Single endpoint:
  POST /mcp  — JSON-RPC requests; responds with JSON or SSE stream
  GET  /mcp  — SSE subscription or JSON discovery (no Accept: text/event-stream)
  DELETE /mcp — session termination (stateless: no-op)

Free tier deployment:
  nginx /mcp → localhost:8082  (no DECKRUN_API_KEY in environment)

Paid tier deployment:
  nginx /mcp → localhost:8082  (DECKRUN_API_KEY set in environment)

Run (development):
    python deckrun_mcp_http.py

Run (production via systemd):
    uvicorn deckrun_mcp_http:starlette_app --host 127.0.0.1 --port 8082

Requirements:
    pip install "mcp[cli]>=1.1.0" uvicorn starlette requests
"""

import asyncio
import contextlib
import json
import logging
import os

import requests
import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
import mcp.types as types
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

logging.basicConfig(level=logging.INFO)

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
OPENAPI_URL = f"{_BASE}/.well-known/openapi.yaml"
SCHEMA_VERSION = "deckrun.v1"
SERVER_NAME = "deckrun" if PAID else "deckrun-free"
SERVER_VERSION = "2.0.0"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


# ---------------------------------------------------------------------------
# Tool definitions (same as stdio version)
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
            "markdown": {"type": "string", "description": "Complete slide deck in Deckrun Markdown format."},
            "voice": {"type": "string", "description": "TTS voice ID (optional)."},
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
            "markdown": {"type": "string", "description": "Complete slide deck in Deckrun Markdown format."},
            "voice": {"type": "string", "description": "TTS voice ID (optional)."},
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
            "job_id": {"type": "string", "description": "Job ID returned by generate_video or generate_audio."},
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

_TOOL_VALIDATE_MARKDOWN = types.Tool(
    name="validate_markdown",
    description=(
        "Lint Deckrun Markdown and get a pre-flight RU estimate before generating. "
        "Returns: valid (bool), issues (lint errors/warnings), estimated_pages, estimated_ru. "
        "Use this before generate_slide_deck or generate_video to catch errors and check RU cost."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "Deckrun Markdown to validate.",
            },
            "document_category": {
                "type": "string",
                "description": "Document category: slide (default), resume, whitepaper, datasheet, patent, generic, book.",
            },
            "output_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Output types to estimate RU for (e.g. ['pdf', 'video', 'audio']). Omit to skip estimate.",
            },
        },
        "required": ["markdown"],
    },
)

_TOOL_LIST_THEMES = types.Tool(
    name="list_themes",
    description=(
        "List available slide/document themes for this account. "
        "Returns system themes and custom themes. "
        "Use the returned id as slide_theme_id in generate_slide_deck or generate_video."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

_TOOL_LIST_VOICES = types.Tool(
    name="list_voices",
    description=(
        "List available narration voices for this account. "
        "Returns voice id, name, tier (system or premium), and language. "
        "Use the returned id as voice in generate_video or generate_audio."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp_server = Server(SERVER_NAME)


@mcp_server.list_resources()
async def list_resources() -> list[types.Resource]:
    tier = "paid" if PAID else "free"
    return [
        types.Resource(
            uri=f"deckrun-{tier}://skill",
            name=f"Deckrun {'Paid' if PAID else 'Free'} Skill Card",
            description="OpenClaw/ClawHub compatible skill YAML. Machine-readable capability declaration.",
            mimeType="application/yaml",
        ),
        types.Resource(
            uri=f"deckrun-{tier}://openapi",
            name="Deckrun OpenAPI Spec",
            description="OpenAPI 3.0 spec. Use to understand request/response shapes.",
            mimeType="application/yaml",
        ),
    ]


@mcp_server.read_resource()
async def read_resource(uri: types.AnyUrl) -> str:
    uri_str = str(uri)
    tier = "paid" if PAID else "free"
    if uri_str == f"deckrun-{tier}://openapi":
        try:
            resp = requests.get(OPENAPI_URL, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            return f"# Error fetching OpenAPI spec: {exc}\n# URL: {OPENAPI_URL}\n"
    raise ValueError(f"Unknown resource URI: {uri_str}")


@mcp_server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = [_TOOL_GET_SLIDE_FORMAT, _TOOL_GENERATE_SLIDE_DECK]
    if PAID:
        tools += [
            _TOOL_GENERATE_VIDEO, _TOOL_GENERATE_AUDIO, _TOOL_CHECK_JOB, _TOOL_GET_ACCOUNT,
            _TOOL_VALIDATE_MARKDOWN, _TOOL_LIST_THEMES, _TOOL_LIST_VOICES,
        ]
    return tools


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "get_slide_format":
        return await _get_slide_format()
    if name == "generate_slide_deck":
        return await _generate_slide_deck(arguments.get("markdown", ""))
    if PAID:
        if name == "generate_video":
            return await _generate_async(arguments.get("markdown", ""), "video", arguments.get("voice"))
        if name == "generate_audio":
            return await _generate_async(arguments.get("markdown", ""), "audio", arguments.get("voice"))
        if name == "check_job":
            return await _check_job(arguments.get("job_id", ""))
        if name == "get_account":
            return await _get_account()
        if name == "validate_markdown":
            return await _validate_markdown(
                arguments.get("markdown", ""),
                arguments.get("document_category"),
                arguments.get("output_types"),
            )
        if name == "list_themes":
            return await _list_themes()
        if name == "list_voices":
            return await _list_voices()
    return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


# ---------------------------------------------------------------------------
# Tool implementations
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
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if not PAID and isinstance(data, dict):
            data["upgrade"] = (
                "Want video, audio, custom themes, and more? "
                "Upgrade at https://agenticdecks.com/pricing — "
                "then set DECKRUN_API_KEY in your MCP config."
            )
            return [types.TextContent(type="text", text=json.dumps(data))]
        return [types.TextContent(type="text", text=resp.text)]
    if resp.status_code == 413:
        return [types.TextContent(type="text", text=json.dumps({
            "error": "INPUT_TOO_LARGE",
            "detail": "More than 10 slides or Markdown exceeds 50 KB. Reduce slides and retry.",
        }))]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


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
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


async def _check_job(job_id: str) -> list[types.TextContent]:
    if not job_id.strip():
        return [types.TextContent(type="text", text='{"error": "job_id is empty"}')]
    try:
        resp = requests.get(f"{_BASE}/jobs/{job_id}", headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


async def _get_account() -> list[types.TextContent]:
    try:
        resp = requests.get(f"{_BASE}/api/profile/me", headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


async def _validate_markdown(
    markdown: str,
    document_category: str | None,
    output_types: list[str] | None,
) -> list[types.TextContent]:
    if not markdown.strip():
        return [types.TextContent(type="text", text='{"error": "markdown is empty"}')]
    payload: dict = {"markdown": markdown}
    if document_category:
        payload["document_category"] = document_category
    if output_types:
        payload["output_types"] = output_types
    try:
        resp = requests.post(f"{_BASE}/validate", json=payload, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code in (200, 400):
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


async def _list_themes() -> list[types.TextContent]:
    try:
        resp = requests.get(f"{_BASE}/slide-themes", headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


async def _list_voices() -> list[types.TextContent]:
    try:
        resp = requests.get(f"{_BASE}/voices", headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    if resp.status_code == 200:
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=json.dumps({
        "error": f"HTTP {resp.status_code}", "detail": resp.text[:300],
    }))]


# ---------------------------------------------------------------------------
# Streamable HTTP transport
# ---------------------------------------------------------------------------

session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    event_store=None,
    json_response=False,
    stateless=True,
)


async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
    await session_manager.handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with session_manager.run():
        yield


# ---------------------------------------------------------------------------
# Discovery middleware
# ---------------------------------------------------------------------------

_SERVER_INFO: dict = {
    "server": SERVER_NAME,
    "version": SERVER_VERSION,
    "tier": "paid" if PAID else "free",
    "transport": "streamable-http",
    "mcp_endpoint": f"{'https://deckrun-mcp.agenticdecks.com' if PAID else 'https://deckrun-mcp-free.agenticdecks.com'}/mcp/",
    "tools": (
        ["get_slide_format", "generate_slide_deck", "generate_video", "generate_audio",
         "check_job", "get_account", "validate_markdown", "list_themes", "list_voices"]
        if PAID else
        ["get_slide_format", "generate_slide_deck"]
    ),
    "docs": "https://agenticdecks.com/docs",
    "repository": "https://github.com/agenticdecks/deckrun-mcp",
}

if not PAID:
    _SERVER_INFO["upgrade"] = {
        "paid_endpoint": "https://deckrun-mcp.agenticdecks.com/mcp/",
        "how": "Set DECKRUN_API_KEY=dk_live_... in your MCP client config.",
        "pricing": "https://agenticdecks.com/pricing",
        "extra_tools": [
            "generate_video", "generate_audio", "check_job",
            "get_account", "validate_markdown", "list_themes", "list_voices",
        ],
    }


class MCPDiscoveryMiddleware(BaseHTTPMiddleware):
    """Return server metadata for GET requests that don't accept text/event-stream."""

    async def dispatch(self, request: Request, call_next):
        if (
            request.method == "GET"
            and "text/event-stream" not in request.headers.get("accept", "")
        ):
            return JSONResponse(_SERVER_INFO)
        return await call_next(request)


starlette_app = Starlette(
    debug=False,
    lifespan=lifespan,
    routes=[Mount("/mcp", app=handle_mcp)],
)

starlette_app.add_middleware(MCPDiscoveryMiddleware)
starlette_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


if __name__ == "__main__":
    uvicorn.run(starlette_app, host="127.0.0.1", port=8082, log_level="info")
