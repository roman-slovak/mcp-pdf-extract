"""FastMCP server exposing PDF extraction tools.

All tools accept a ``path`` argument (absolute or relative to the server's CWD) and
return small, JSON-serializable payloads sized to fit the 40KB response budget
defined in :mod:`pdf_utils`. Page numbers are 1-indexed in the public API.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import pdfplumber
import pypdf
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .pdf_utils import (
    RESPONSE_BUDGET_BYTES,
    PdfPathError,
    normalize_range,
    resolve_pdf_path,
    truncate_to_budget,
)

mcp = FastMCP("pdf-extract")

# The single most important rule the model must learn — surfaced in every tool's schema
# so a weak model sees it on every call, not just once in a system prompt.
_PATH_DESCRIPTION = (
    "Absolute path to a PDF file. MUST start with '/' (or '~/'). The server does NOT "
    "search anywhere — bare filenames will fail. If the user gave only a filename, "
    "assume the file is in /Users/roman/Downloads/ and pass the full path."
)
PathArg = Annotated[str, Field(description=_PATH_DESCRIPTION)]

# Anti-hallucination nudge baked into every metadata-only response. A weak model that
# only sees title/subject will otherwise invent contents from the title alone.
_CONTENT_HINT = (
    "This response contains NO document text. Title and subject are NOT the contents. "
    "To answer ANY question about what the document says or contains, you MUST call "
    "pdf__extract_text (or pdf__search_text) on this path before replying."
)


def _stringify(value: Any) -> str | None:
    """Best-effort string conversion for pypdf metadata values (which may be PDF objects)."""
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None


@mcp.tool()
def get_metadata(path: PathArg) -> dict[str, Any]:
    """Return PDF metadata ONLY: title, author, dates, page count, encryption status.

    Does NOT return document text or content. The title is a label, not a summary.
    To answer "what is in the document", "summarize", or any question about contents,
    call ``pdf__extract_text`` after this — do not guess from the title.
    """
    pdf_path = resolve_pdf_path(path)
    reader = pypdf.PdfReader(str(pdf_path))
    meta = reader.metadata
    return {
        "path": str(pdf_path),
        "page_count": len(reader.pages),
        "encrypted": reader.is_encrypted,
        "title": _stringify(meta.title) if meta else None,
        "author": _stringify(meta.author) if meta else None,
        "subject": _stringify(meta.subject) if meta else None,
        "creator": _stringify(meta.creator) if meta else None,
        "producer": _stringify(meta.producer) if meta else None,
        "creation_date": _stringify(meta.creation_date) if meta else None,
        "modification_date": _stringify(meta.modification_date) if meta else None,
        "note": _CONTENT_HINT,
    }


@mcp.tool()
def get_page_count(path: PathArg) -> dict[str, Any]:
    """Return the number of pages in the PDF.

    Does NOT return any text. Use ``pdf__extract_text`` to actually read pages.
    """
    pdf_path = resolve_pdf_path(path)
    reader = pypdf.PdfReader(str(pdf_path))
    return {"page_count": len(reader.pages), "note": _CONTENT_HINT}


def _extract_page_text(page: pdfplumber.page.Page) -> str:
    """Extract text from a pdfplumber page, falling back to pypdf if empty."""
    text = page.extract_text() or ""
    if text.strip():
        return text
    # Fallback: pypdf occasionally gets text where pdfplumber returns nothing
    # (e.g. some PDFs with unusual font encodings). page.page_number is 1-indexed.
    try:
        reader = pypdf.PdfReader(page.pdf.stream)
        return reader.pages[page.page_number - 1].extract_text() or ""
    except Exception:
        return text


@mcp.tool()
def extract_text(
    path: PathArg,
    page: Annotated[int, Field(description="1-indexed page to start from.", ge=1)] = 1,
    end_page: Annotated[
        int | None,
        Field(
            description=(
                "1-indexed last page (inclusive). Omit to extract only `page`. "
                "Large ranges may stop early — watch `has_more` and `next_page` "
                "in the response and call again from `next_page` to resume."
            ),
            ge=1,
        ),
    ] = None,
) -> dict[str, Any]:
    """Extract the actual text from a page range. THIS is the tool to call whenever
    the user asks "what is in the document", "summarize", "what does it say", or any
    question about contents. Do not infer contents from the title or metadata.

    Page numbers are 1-indexed, inclusive. Stops early if the accumulated response
    would exceed ~40KB and reports ``has_more`` with ``next_page`` so you can call
    again from ``next_page`` to keep reading. Single-page calls always succeed even
    if that page is huge (the page text gets truncated with a marker).
    """
    pdf_path = resolve_pdf_path(path)
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        start_0, end_0 = normalize_range(page, end_page, page_count)

        pages_out: list[dict[str, Any]] = []
        running_bytes = 0
        has_more = False
        next_page: int | None = None

        for idx in range(start_0, end_0 + 1):
            text = _extract_page_text(pdf.pages[idx])
            entry = {"page": idx + 1, "text": text}
            entry_size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))

            if running_bytes + entry_size > RESPONSE_BUDGET_BYTES and pages_out:
                # We already have at least one page — stop here and let caller resume.
                has_more = True
                next_page = idx + 1
                break

            if entry_size > RESPONSE_BUDGET_BYTES:
                # Single page is too big; truncate its text in place.
                truncated, _ = truncate_to_budget(text)
                entry = {"page": idx + 1, "text": truncated, "truncated": True}
                entry_size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))

            pages_out.append(entry)
            running_bytes += entry_size

        completed_range = (
            not has_more
            and pages_out
            and pages_out[-1]["page"] == end_0 + 1
            and end_0 + 1 < page_count
        )
        if completed_range:
            # Hint the caller toward the page after the last one we returned.
            next_page = end_0 + 2

    return {
        "path": str(pdf_path),
        "page_count": page_count,
        "pages": pages_out,
        "has_more": has_more,
        "next_page": next_page,
    }


@mcp.tool()
def extract_tables(
    path: PathArg,
    page: Annotated[int, Field(description="1-indexed page number.", ge=1)] = 1,
) -> dict[str, Any]:
    """Extract tables from a single page as a list of 2D string arrays.

    Drops trailing rows / tables if the JSON-encoded response would exceed the budget,
    and sets ``truncated`` so the caller knows.
    """
    pdf_path = resolve_pdf_path(path)
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        page_0, _ = normalize_range(page, None, page_count)
        raw_tables = pdf.pages[page_0].extract_tables() or []

    # Normalize cells: None → "" for stable JSON.
    tables: list[list[list[str]]] = [
        [[cell if cell is not None else "" for cell in row] for row in table]
        for table in raw_tables
    ]

    # Fit to budget by dropping rows from the tail, then whole tables if needed.
    truncated = False
    while tables:
        payload = {"tables": tables}
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= RESPONSE_BUDGET_BYTES:
            break
        truncated = True
        if len(tables[-1]) > 1:
            tables[-1].pop()
        else:
            tables.pop()

    return {
        "path": str(pdf_path),
        "page": page_0 + 1,
        "tables": tables,
        "table_count": len(tables),
        "truncated": truncated,
    }


@mcp.tool()
def search_text(
    path: PathArg,
    query: Annotated[str, Field(description="Substring to search for (case-insensitive).")],
    max_matches: Annotated[
        int, Field(description="Maximum number of snippets to return.", ge=1)
    ] = 20,
    context_chars: Annotated[
        int,
        Field(description="Characters of context on each side of the match.", ge=0),
    ] = 120,
) -> dict[str, Any]:
    """Case-insensitive substring search across all pages.

    Returns up to ``max_matches`` snippets, each with ``page`` and a ``snippet`` window
    of ``context_chars`` on either side of the match. Stops early on budget overflow.
    """
    if not query:
        raise PdfPathError("query must be a non-empty string")

    pdf_path = resolve_pdf_path(path)
    needle = query.lower()
    matches: list[dict[str, Any]] = []
    total_matches = 0
    truncated = False

    # Reserve headroom for the wrapper fields (path, query, total_matches, truncated).
    # The exact wrapper is small but path can be long, so measure it once.
    wrapper_overhead = len(
        json.dumps(
            {
                "path": str(pdf_path),
                "query": query,
                "matches": [],
                "total_matches": 0,
                "truncated": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )
    match_budget = RESPONSE_BUDGET_BYTES - wrapper_overhead - 64  # small safety margin

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page_obj in enumerate(pdf.pages):
            text = _extract_page_text(page_obj)
            if not text:
                continue
            haystack = text.lower()
            pos = 0
            while True:
                hit = haystack.find(needle, pos)
                if hit == -1:
                    break
                total_matches += 1
                if len(matches) < max_matches and not truncated:
                    start = max(0, hit - context_chars)
                    end = min(len(text), hit + len(query) + context_chars)
                    snippet = text[start:end].strip()
                    entry = {"page": page_idx + 1, "snippet": snippet}
                    candidate = matches + [entry]
                    if (
                        len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))
                        > match_budget
                    ):
                        truncated = True
                    else:
                        matches.append(entry)
                pos = hit + len(needle)

    return {
        "path": str(pdf_path),
        "query": query,
        "matches": matches,
        "total_matches": total_matches,
        "truncated": truncated or total_matches > len(matches),
    }


def _flatten_outline(
    items: list[Any], reader: pypdf.PdfReader, level: int = 0
) -> list[dict[str, Any]]:
    """Recursively flatten pypdf's nested outline structure into a flat list."""
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, list):
            out.extend(_flatten_outline(item, reader, level + 1))
            continue
        title = _stringify(getattr(item, "title", None)) or ""
        page_num: int | None = None
        try:
            raw = reader.get_destination_page_number(item)
            page_num = raw + 1 if raw is not None else None
        except Exception:
            page_num = None
        out.append({"title": title, "page": page_num, "level": level})
    return out


@mcp.tool()
def get_outline(path: PathArg) -> dict[str, Any]:
    """Return the PDF's table of contents (bookmarks) as a flat list, or empty if none.

    Outline titles are chapter labels, not contents. Use ``pdf__extract_text`` on
    the page numbers from the outline to actually read those sections.
    """
    pdf_path = resolve_pdf_path(path)
    reader = pypdf.PdfReader(str(pdf_path))
    try:
        flat = _flatten_outline(list(reader.outline), reader)
    except Exception:
        flat = []
    return {"path": str(pdf_path), "outline": flat, "note": _CONTENT_HINT}


def main() -> None:
    """Entry point for ``mcp-pdf-extract`` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
