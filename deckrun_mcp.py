#!/usr/bin/env python3
"""
Deckrun Free — MCP server.

Tools (callable by Claude):
  get_slide_format    — fetches live Deckrun slide format spec (layout tags, rules, example)
  generate_slide_deck — posts Deckrun Markdown to /free/generate, returns PDF URL + metadata

Resources (readable by agent registries and orchestrators):
  deckrun-free://skill    — OpenClaw/ClawHub compatible skill YAML (machine-readable capability card)
  deckrun-free://openapi  — trimmed OpenAPI spec for the free endpoint

The two-tool pattern lets Claude fetch the live spec at inference time so format
rules never go stale. The resources enable agent registry discovery without
requiring a separate HTTP service.

Requirements:
    pip install mcp requests

Setup (Claude Desktop):
    Edit ~/Library/Application Support/Claude/claude_desktop_config.json
    (see agents/README.md for the exact snippet)

Setup (Cursor):
    Edit .cursor/mcp.json in your project root
    (see agents/README.md for the exact snippet)
"""

import asyncio
import json

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

DECKRUN_API = "https://free.agenticdecks.com/free/generate"
SCHEMA_URL = "https://agenticdecks.com/schemas/v1/deckrun-slide-format.json"
OPENAPI_URL = "https://free.agenticdecks.com/.well-known/openapi.yaml"
SCHEMA_VERSION = "deckrun.v1"

# OpenClaw / ClawHub skill card — machine-readable capability declaration.
# Returned as deckrun-free://skill resource so orchestrators can auto-discover
# and register this capability without human explanation.
SKILL_YAML = """\
id: deckrun-pdf-generator-free

name: Deckrun PDF Generator (Free)

description: >
  Generate a presentation-quality PDF from Deckrun Markdown.
  Designed for AI agents. No authentication required.
  Returns a public, time-limited URL to the generated PDF.

provider:
  name: Agentic Decks
  url: https://agenticdecks.com
  contact: https://agenticdecks.com/contact

access:
  type: public
  auth: none
  cost: free
  rate_limits:
    requests_per_minute: 10
    notes: Fair-use enforced via nginx

capability:
  type: transformation
  input_format: text/markdown
  output_format: application/pdf
  deterministic: true
  stateful: false

endpoint:
  method: POST
  url: https://free.agenticdecks.com/free/generate
  content_type: application/json

input_schema:
  type: object
  required: [markdown]
  properties:
    markdown:
      type: string
      maxLength: 51200
      description: >
        Deckrun Markdown source. Slides separated by ---. Maximum 10 slides.
    schema_version:
      type: string
      description: Declare format version (send "deckrun.v1"). Echoed in response.

output_schema:
  type: object
  required: [url, slug]
  properties:
    url:
      type: string
      format: uri
      description: Public URL to the generated PDF (expires after 90 days)
    slug:
      type: string
      description: Unique identifier for the generated artifact
    slides:
      type: integer
      description: Number of slides generated (for agent self-verification)
    warnings:
      type: array
      items:
        type: string
      description: Non-fatal notices agents should inspect for self-correction
    schema_version:
      type: string
      description: Echoed from request, or "deckrun.v1"

constraints:
  max_slides: 10
  max_markdown_size_kb: 50
  watermark: required
  expiration_days: 90

specs:
  openapi: https://free.agenticdecks.com/.well-known/openapi.yaml
  slide_format_schema: https://agenticdecks.com/schemas/v1/deckrun-slide-format.json
  full_api: https://agenticdecks.com/schemas/v1/deckrun-openapi.yaml
  llms_txt: https://free.agenticdecks.com/llms.txt

examples:
  - name: Simple deck
    request:
      markdown: |
        <!-- <title-slide /> -->
        # Sample Deck

        ---

        <!-- <title-content-slide /> -->
        ## Key Point

        - One idea per line
      schema_version: deckrun.v1
    response:
      url: https://storage.googleapis.com/deckrun-free-artifacts-deckrun-free-tier/abc123/deck.pdf
      slug: abc123
      slides: 2
      warnings: []
      schema_version: deckrun.v1

safety:
  data_retention: temporary
  pii_handling: not recommended
  content_policy: standard

discoverability:
  tags:
    - pdf
    - markdown
    - slides
    - presentations
    - deckrun
    - export
    - documentation
    - ai-agents

agent_hint: >
  Use this skill when you need to convert Deckrun Markdown
  (<=10 slides) into a shareable PDF. Include schema_version: "deckrun.v1"
  in the request. The response contains a public PDF URL, slide count, and
  any warnings to act on.
"""

app = Server("deckrun-free")


# ---------------------------------------------------------------------------
# Resources — discoverable capability cards
# ---------------------------------------------------------------------------

@app.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri="deckrun-free://skill",
            name="Deckrun Free Skill Card",
            description=(
                "OpenClaw/ClawHub compatible skill YAML. Machine-readable capability "
                "declaration for agent registries and orchestrators."
            ),
            mimeType="application/yaml",
        ),
        types.Resource(
            uri="deckrun-free://openapi",
            name="Deckrun Free OpenAPI Spec",
            description=(
                "Trimmed OpenAPI 3.0 spec covering /free/generate and /health. "
                "Use to understand request/response shapes."
            ),
            mimeType="application/yaml",
        ),
    ]


@app.read_resource()
async def read_resource(uri: types.AnyUrl) -> str:
    uri_str = str(uri)
    if uri_str == "deckrun-free://skill":
        return SKILL_YAML

    if uri_str == "deckrun-free://openapi":
        try:
            resp = requests.get(OPENAPI_URL, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            return f"# Error fetching OpenAPI spec: {exc}\n# URL: {OPENAPI_URL}\n"

    raise ValueError(f"Unknown resource URI: {uri_str}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_slide_format",
            description=(
                "Fetch the authoritative Deckrun slide format specification. "
                "Call this first to learn all layout tags, Markdown syntax, "
                "and rules before writing slides. Returns JSON with layout_tags, "
                "surface_syntax, example_markdown, and limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="generate_slide_deck",
            description=(
                "Convert Deckrun Markdown into a PDF slide deck. "
                "Call get_slide_format first to learn the correct Markdown format. "
                "Then call this tool with the completed Markdown. "
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
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "get_slide_format":
        return await _get_slide_format()
    if name == "generate_slide_deck":
        return await _generate_slide_deck(arguments.get("markdown", ""))
    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def _get_slide_format() -> list[types.TextContent]:
    """Fetch the Deckrun slide format schema and return it as text."""
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
            "limits": {
                "max_slides": 10,
                "max_body_size_kb": 50,
                "pdf_expiry_days": 90,
            },
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
            "heading_convention": (
                "# (H1) for title slide title, ## (H2) for all other slide headings."
            ),
            "limits": {"max_slides": 10, "max_body_size_kb": 50},
            "schema_version": SCHEMA_VERSION,
        }
        return [types.TextContent(type="text", text=json.dumps(fallback, indent=2))]


async def _generate_slide_deck(markdown: str) -> list[types.TextContent]:
    """POST to /free/generate and return the result."""
    if not markdown.strip():
        return [types.TextContent(type="text", text='{"error": "markdown is empty"}')]

    try:
        resp = requests.post(
            DECKRUN_API,
            json={"markdown": markdown, "schema_version": SCHEMA_VERSION},
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
    except requests.RequestException as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, dict):
                data["upgrade"] = (
                    "Want video, audio, custom themes, and more? "
                    "Upgrade at https://agenticdecks.com/pricing — "
                    "then set DECKRUN_API_KEY in your MCP config."
                )
                return [types.TextContent(type="text", text=json.dumps(data))]
        except (ValueError, KeyError):
            pass
        return [types.TextContent(type="text", text=resp.text)]

    if resp.status_code == 413:
        msg = (
            '{"error": "INPUT_TOO_LARGE", '
            '"detail": "More than 10 slides or Markdown exceeds 50 KB. '
            'Reduce the number of slides and try again."}'
        )
        return [types.TextContent(type="text", text=msg)]

    return [types.TextContent(
        type="text",
        text=json.dumps({"error": f"HTTP {resp.status_code}", "detail": resp.text[:300]}),
    )]


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
