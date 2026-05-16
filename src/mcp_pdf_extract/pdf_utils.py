"""Helpers for path resolution, page-range normalization, and byte-budget enforcement."""

from __future__ import annotations

from pathlib import Path

# 50KB host limit (mcp_ollama_host/mcp_pool.py:MAX_RESULT_BYTES) minus ~10KB rezerva
# pre JSON-encoding overhead a wrapping. Tools nesmú vrátiť odpoveď väčšiu ako toto.
RESPONSE_BUDGET_BYTES = 40_000


class PdfPathError(ValueError):
    """Raised when a provided path is not a usable PDF file."""


def resolve_pdf_path(path: str) -> Path:
    """Expand ``~``, resolve to absolute path, and validate that it points to a PDF file.

    Validation kept liberal: existing regular file ending in ``.pdf`` (case-insensitive).
    Magic-byte sniffing is the PDF library's job — duplicating it here would only desync.
    """
    if not path or not path.strip():
        raise PdfPathError("path must be a non-empty string")

    resolved = Path(path).expanduser().resolve()

    if not resolved.exists():
        raise PdfPathError(f"file not found: {resolved}")
    if not resolved.is_file():
        raise PdfPathError(f"not a regular file: {resolved}")
    if resolved.suffix.lower() != ".pdf":
        raise PdfPathError(f"not a PDF (suffix must be .pdf): {resolved.name}")

    return resolved


def clamp_page(page: int, page_count: int) -> int:
    """Convert 1-indexed page to 0-indexed, clamped to ``[0, page_count - 1]``.

    Raises ``PdfPathError`` if the document has no pages.
    """
    if page_count <= 0:
        raise PdfPathError("document has no pages")
    if page < 1:
        return 0
    if page > page_count:
        return page_count - 1
    return page - 1


def normalize_range(
    start: int, end: int | None, page_count: int
) -> tuple[int, int]:
    """Return inclusive 0-indexed ``(start, end)`` after clamping.

    Accepts 1-indexed inputs. ``end=None`` means single page. Swaps if reversed.
    """
    s = clamp_page(start, page_count)
    if end is None:
        return s, s
    e = clamp_page(end, page_count)
    if e < s:
        s, e = e, s
    return s, e


def within_budget(payload_size: int, budget: int = RESPONSE_BUDGET_BYTES) -> bool:
    """Return True if a payload of ``payload_size`` bytes fits the response budget."""
    return payload_size <= budget


def truncate_to_budget(text: str, budget: int = RESPONSE_BUDGET_BYTES) -> tuple[str, bool]:
    """Truncate ``text`` so its UTF-8 encoding fits ``budget`` bytes.

    Returns ``(truncated_text, was_truncated)``. Tries to cut on a whitespace boundary
    near the limit so we don't slice in the middle of a multi-byte sequence or word.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text, False

    # Walk back from the budget to the previous whitespace to avoid splitting words /
    # multi-byte chars. Decode with errors="ignore" guards the byte-boundary case.
    cut = encoded[:budget].decode("utf-8", errors="ignore")
    last_ws = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    if last_ws > budget // 2:
        cut = cut[:last_ws]
    return cut + "\n[...truncated]", True
