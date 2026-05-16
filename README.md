# mcp-pdf-extract

A small, focused MCP server that gives language models the ability to read PDF files.
Built for use with [`mcp-ollama-host`](../mcp-ollama-host) and weaker local models that
need predictable, page-sized chunks of PDF content.

## What it does

Stdio MCP server with six tools. Every tool returns a JSON payload sized to fit comfortably
under the 50 KB response limit enforced by `mcp-ollama-host`, so the host never has to
truncate mid-response.

| Tool                | Purpose                                                            |
|---------------------|--------------------------------------------------------------------|
| `get_metadata`      | Title, author, dates, page count, encryption flag.                 |
| `get_page_count`    | Just the page count — useful before planning a sweep.              |
| `extract_text`      | Text of one page or an inclusive range. Returns `has_more` / `next_page` when the range is too big to fit. |
| `extract_tables`    | Tables on a single page, as 2-D string arrays (via `pdfplumber`).  |
| `search_text`       | Case-insensitive substring search across all pages, with snippets. |
| `get_outline`       | Flat list of bookmarks (title, page, level).                       |

All page numbers are 1-indexed in the public API.

## Install

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```sh
git clone <this-repo> ~/Developer/mcp-pdf-extract
cd ~/Developer/mcp-pdf-extract
uv sync
```

## Use with `mcp-ollama-host`

Add an entry to `~/.config/mcp-ollama-host/config.json`:

```json
{
  "mcpServers": {
    "pdf": {
      "command": "uv",
      "args": ["--directory", "/Users/roman/Developer/mcp-pdf-extract", "run", "mcp-pdf-extract"],
      "trusted": true
    }
  }
}
```

After restart, the host exposes the tools to the model as `pdf__get_metadata`,
`pdf__extract_text`, etc.

## Run standalone

```sh
uv run mcp-pdf-extract            # stdio
# or
uv run python -m mcp_pdf_extract  # same thing
```

## Development

```sh
uv run pytest                     # 43 tests, ~40s (most time is fixture PDF generation)
uv run ruff check
uv run mypy src/
```

Fixtures (`tests/fixtures/*.pdf`) are generated on first run via `reportlab` and cached.
Delete them to regenerate.

## Design notes

- Hard cap of 40 KB per response (`pdf_utils.RESPONSE_BUDGET_BYTES`) leaves headroom under
  the host's 50 KB limit.
- `extract_text` over a large range stops at the budget and returns `has_more=True` with a
  `next_page` pointer so the model can resume sequentially.
- No state between calls — each tool opens the PDF fresh. PDFs are small, this is local
  I/O, and stateless tools are easier to reason about.

## Out of scope

- OCR for scanned PDFs (consider a separate server with `pytesseract` + `pdf2image`).
- Image extraction.
- Caching of opened documents.
