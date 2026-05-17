"""Tests for the FastMCP tool implementations.

Tools are called directly as plain Python functions (FastMCP's ``@mcp.tool()`` doesn't
rewrap the function signature, so the wrapped function remains importable and callable).
That keeps tests fast and stays out of the transport plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_pdf_extract.pdf_utils import RESPONSE_BUDGET_BYTES, PdfPathError
from mcp_pdf_extract.server import (
    extract_tables,
    extract_text,
    get_metadata,
    get_outline,
    get_page_count,
    search_text,
)


class TestGetMetadata:
    def test_returns_expected_fields(self, simple_pdf: Path) -> None:
        meta = get_metadata(str(simple_pdf))
        assert meta["page_count"] == 3
        assert meta["encrypted"] is False
        assert meta["title"] == "Simple Test Document"
        assert meta["author"] == "mcp-pdf-extract tests"
        assert meta["subject"] == "Fixture for unit tests"
        assert meta["path"].endswith("simple.pdf")

    def test_invalid_path_raises(self) -> None:
        with pytest.raises(PdfPathError):
            get_metadata("/nonexistent/file.pdf")


class TestGetPageCount:
    def test_simple_pdf(self, simple_pdf: Path) -> None:
        result = get_page_count(str(simple_pdf))
        assert result["page_count"] == 3
        assert "note" in result  # anti-hallucination nudge

    def test_large_pdf(self, large_pdf: Path) -> None:
        assert get_page_count(str(large_pdf))["page_count"] == 80


class TestExtractText:
    def test_single_page_default(self, simple_pdf: Path) -> None:
        result = extract_text(str(simple_pdf))
        assert result["page_count"] == 3
        assert len(result["pages"]) == 1
        assert result["pages"][0]["page"] == 1
        assert "First page header" in result["pages"][0]["text"]
        assert result["has_more"] is False
        assert result["next_page"] == 2

    def test_specific_page(self, simple_pdf: Path) -> None:
        result = extract_text(str(simple_pdf), page=3)
        assert result["pages"][0]["page"] == 3
        assert "Searchable_marker" in result["pages"][0]["text"]
        assert result["has_more"] is False
        assert result["next_page"] is None

    def test_inclusive_range(self, simple_pdf: Path) -> None:
        result = extract_text(str(simple_pdf), page=1, end_page=3)
        assert [p["page"] for p in result["pages"]] == [1, 2, 3]
        assert result["has_more"] is False

    def test_out_of_range_clamps(self, simple_pdf: Path) -> None:
        result = extract_text(str(simple_pdf), page=99)
        assert result["pages"][0]["page"] == 3

    def test_response_fits_budget(self, large_pdf: Path) -> None:
        result = extract_text(str(large_pdf), page=1, end_page=80)
        size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        assert size <= RESPONSE_BUDGET_BYTES
        # Either we got all pages, or has_more is set with next_page.
        if result["has_more"]:
            assert result["next_page"] is not None
            assert 1 < result["next_page"] <= 80

    def test_has_more_resumable(self, large_pdf: Path) -> None:
        """After hitting has_more, calling again from next_page yields fresh content."""
        first = extract_text(str(large_pdf), page=1, end_page=80)
        assert first["has_more"] is True
        second = extract_text(str(large_pdf), page=first["next_page"], end_page=80)
        # First page of the resumed call should be ``next_page`` from the first call.
        assert second["pages"][0]["page"] == first["next_page"]

    def test_invalid_path_raises(self) -> None:
        with pytest.raises(PdfPathError):
            extract_text("/nope.pdf")


class TestExtractTables:
    def test_extracts_table(self, table_pdf: Path) -> None:
        result = extract_tables(str(table_pdf), page=1)
        assert result["table_count"] >= 1
        assert result["page"] == 1
        # Find a table that contains the header row we put in.
        flattened = [cell for table in result["tables"] for row in table for cell in row]
        assert "Name" in flattened
        assert "Apple" in flattened
        assert result["truncated"] is False

    def test_no_tables_on_page(self, simple_pdf: Path) -> None:
        result = extract_tables(str(simple_pdf), page=1)
        assert result["table_count"] == 0
        assert result["tables"] == []

    def test_response_fits_budget(self, table_pdf: Path) -> None:
        result = extract_tables(str(table_pdf), page=1)
        size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        assert size <= RESPONSE_BUDGET_BYTES


class TestSearchText:
    def test_finds_unique_match(self, simple_pdf: Path) -> None:
        result = search_text(str(simple_pdf), query="Searchable_marker")
        assert result["total_matches"] == 1
        assert len(result["matches"]) == 1
        assert result["matches"][0]["page"] == 3
        assert "Searchable_marker" in result["matches"][0]["snippet"]
        assert result["truncated"] is False

    def test_case_insensitive(self, simple_pdf: Path) -> None:
        result = search_text(str(simple_pdf), query="QUICK BROWN")
        assert result["total_matches"] >= 1

    def test_no_matches(self, simple_pdf: Path) -> None:
        result = search_text(str(simple_pdf), query="zzzznotfoundzzzz")
        assert result["total_matches"] == 0
        assert result["matches"] == []

    def test_empty_query_raises(self, simple_pdf: Path) -> None:
        with pytest.raises(PdfPathError):
            search_text(str(simple_pdf), query="")

    def test_max_matches_respected(self, large_pdf: Path) -> None:
        result = search_text(str(large_pdf), query="quick", max_matches=5)
        assert len(result["matches"]) <= 5
        # large_pdf has many occurrences across 80 pages
        assert result["total_matches"] > 5
        assert result["truncated"] is True

    def test_response_fits_budget(self, large_pdf: Path) -> None:
        result = search_text(str(large_pdf), query="quick", max_matches=10000)
        size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        assert size <= RESPONSE_BUDGET_BYTES


class TestGetOutline:
    def test_pdf_without_outline_returns_empty(self, simple_pdf: Path) -> None:
        result = get_outline(str(simple_pdf))
        assert result["outline"] == []


class TestServerExposesExpectedTools:
    """Sanity check that the FastMCP instance was wired up correctly."""

    def test_tool_registry(self) -> None:
        import asyncio

        from mcp_pdf_extract.server import mcp

        # FastMCP stores tools internally; the public API is async list_tools().
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        expected = {
            "get_metadata",
            "get_page_count",
            "extract_text",
            "extract_tables",
            "search_text",
            "get_outline",
        }
        assert expected.issubset(names), f"missing tools: {expected - names}"
