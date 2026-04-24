# Deckrun MCP Server

[![PyPI](https://img.shields.io/pypi/v/deckrun-mcp)](https://pypi.org/project/deckrun-mcp/)
[![awesome-mcp-servers](https://img.shields.io/badge/awesome--mcp--servers-listed-FC60A8)](https://github.com/punkpeye/awesome-mcp-servers)

> **Note:** The hosted Deckrun endpoints (`free.agenticdecks.com`, `deckrun-mcp-free.agenticdecks.com`, `api.agenticdecks.com`) are provisioned per engagement — see [Access & Availability](https://agenticdecks.com/status/). The `pip install deckrun-mcp` package and local-execution paths documented below work immediately against your own deployment. For hosted access, open an [Access Request](https://github.com/agenticdecks/deckrun/issues/new?template=access.yml) — we respond within one business day.

MCP server for [Deckrun](https://agenticdecks.com) by [Agentic Decks](https://agenticdecks.com) — generate presentation PDFs, narrated videos, and audio from Markdown. Built for AI agents and IDEs.

Deckrun is a document execution engine: your AI writes the content, Deckrun renders it into pixel-perfect branded PDFs, narrated MP4 videos, and MP3 audio — from a single Markdown source. No slide editor, no video tool, no audio studio.

**Install:** `pip install deckrun-mcp`

---

## Quickstart — no install needed

The HTTP transport is hosted and ready. Add one JSON snippet to your IDE.

### VS Code (GitHub Copilot Chat — v1.99+)

`.vscode/mcp.json` in your project (this file is included in the repo):

```json
{
  "servers": {
    "deckrun": {
      "type": "http",
      "url": "https://deckrun-mcp-free.agenticdecks.com/mcp/"
    }
  }
}
```

### Cursor

`.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "deckrun": {
      "url": "https://deckrun-mcp-free.agenticdecks.com/mcp/"
    }
  }
}
```

### Google Antigravity (Gemini CLI)

`~/.gemini/antigravity/mcp_config.json`:

```json
{
  "mcpServers": {
    "deckrun": {
      "serverUrl": "https://deckrun-mcp-free.agenticdecks.com/mcp/"
    }
  }
}
```

### Claude Code (terminal)

`~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "deckrun": {
      "type": "http",
      "url": "https://deckrun-mcp-free.agenticdecks.com/mcp/"
    }
  }
}
```

---

## Stdio install (Claude Desktop and other stdio-only clients)

```bash
pip install deckrun-mcp
```

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "deckrun": {
      "command": "python",
      "args": ["/path/to/deckrun_mcp.py"]
    }
  }
}
```

**Paid tier** — add the API key:

```json
{
  "mcpServers": {
    "deckrun": {
      "command": "python",
      "args": ["/path/to/deckrun_mcp.py"],
      "env": { "DECKRUN_API_KEY": "dk_live_..." }
    }
  }
}
```

Get your API key at [agenticdecks.com](https://agenticdecks.com) after subscribing.

---

## Tools

### Free tier (no key required)

| Tool | Description |
|------|-------------|
| `get_slide_format` | Fetch the live slide format spec — layout tags, syntax rules, example Markdown |
| `generate_slide_deck` | Convert Deckrun Markdown → PDF. Returns a public URL (90-day expiry) |

### Paid tier (`DECKRUN_API_KEY` set)

All free tools plus:

| Tool | Description |
|------|-------------|
| `generate_video` | Markdown → narrated MP4 (async, returns `job_id`) |
| `generate_audio` | Slide notes → MP3 narration (async, returns `job_id`) |
| `check_job` | Poll async job status until `complete` or `failed` |
| `get_account` | Plan name, render units used/remaining, active add-ons |
| `validate_markdown` | Lint Deckrun Markdown and get a pre-flight RU estimate |
| `list_themes` | List available slide/document themes (system + custom) |
| `list_voices` | List available narration voices — id, name, tier, language |

---

## Example prompt

Once configured, ask your AI:

> "Create a 6-slide deck on the future of edge computing"

The AI will call `get_slide_format` to learn the syntax, write the Markdown,
call `generate_slide_deck`, and reply with a clickable PDF link.

---

## HTTP endpoints

| Tier | MCP endpoint |
|------|-------------|
| Free | `https://deckrun-mcp-free.agenticdecks.com/mcp/` |
| Paid | `https://deckrun-mcp.agenticdecks.com/mcp/` |

Discovery: `GET <endpoint>` returns server metadata as JSON.

---

## Links

- [Agentic Decks](https://agenticdecks.com) — product homepage
- [Free tier](https://free.agenticdecks.com) — generate PDFs instantly, no sign-up
- [Slide Background Designer](https://free.agenticdecks.com/tools/bg-designer.html) — free tool to design slide backgrounds
- [Blog: Generate a Free PDF from Claude Code](https://agenticdecks.com/blog/free-pdf-with-deckrun-mcp/) — step-by-step guide
- [Slide format reference](https://agenticdecks.com/deckrun) — layout tags, syntax rules, examples
- [Pricing](https://agenticdecks.com/pricing) — plans from $25/month
- [Documentation](https://agenticdecks.com/docs) — API docs and how-to guides
- [PyPI](https://pypi.org/project/deckrun-mcp/) — `pip install deckrun-mcp`
