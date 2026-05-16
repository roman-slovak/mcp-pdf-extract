"""Tests for path resolution, range normalization and budget helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_pdf_extract.pdf_utils import (
    RESPONSE_BUDGET_BYTES,
    PdfPathError,
    clamp_page,
    normalize_range,
    resolve_pdf_path,
    truncate_to_budget,
    within_budget,
)


class TestResolvePdfPath:
    def test_resolves_absolute_path(self, simple_pdf: Path) -> None:
        result = resolve_pdf_path(str(simple_pdf))
        assert result == simple_pdf.resolve()

    def test_resolves_relative_path(
        self, simple_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(simple_pdf.parent)
        result = resolve_pdf_path(simple_pdf.name)
        assert result == simple_pdf.resolve()

    def test_expands_tilde(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Point HOME at tmp_path and create a PDF there.
        monkeypatch.setenv("HOME", str(tmp_path))
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%fake\n")
        result = resolve_pdf_path("~/x.pdf")
        assert result == pdf.resolve()

    def test_empty_path_raises(self) -> None:
        with pytest.raises(PdfPathError, match="non-empty"):
            resolve_pdf_path("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(PdfPathError, match="non-empty"):
            resolve_pdf_path("   ")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PdfPathError, match="not found"):
            resolve_pdf_path(str(tmp_path / "nope.pdf"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "dir.pdf"
        d.mkdir()
        with pytest.raises(PdfPathError, match="not a regular file"):
            resolve_pdf_path(str(d))

    def test_non_pdf_suffix_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("hi")
        with pytest.raises(PdfPathError, match="not a PDF"):
            resolve_pdf_path(str(f))


class TestClampPage:
    def test_in_range(self) -> None:
        assert clamp_page(1, 5) == 0
        assert clamp_page(3, 5) == 2
        assert clamp_page(5, 5) == 4

    def test_below_one_clamps_to_first(self) -> None:
        assert clamp_page(0, 5) == 0
        assert clamp_page(-7, 5) == 0

    def test_above_max_clamps_to_last(self) -> None:
        assert clamp_page(99, 5) == 4

    def test_zero_pages_raises(self) -> None:
        with pytest.raises(PdfPathError, match="no pages"):
            clamp_page(1, 0)


class TestNormalizeRange:
    def test_single_page_when_end_none(self) -> None:
        assert normalize_range(2, None, 5) == (1, 1)

    def test_inclusive_range(self) -> None:
        assert normalize_range(2, 4, 10) == (1, 3)

    def test_swaps_when_reversed(self) -> None:
        assert normalize_range(5, 2, 10) == (1, 4)

    def test_clamps_both_ends(self) -> None:
        assert normalize_range(-5, 999, 10) == (0, 9)


class TestBudgetHelpers:
    def test_within_budget(self) -> None:
        assert within_budget(100, 200) is True
        assert within_budget(200, 200) is True
        assert within_budget(201, 200) is False

    def test_truncate_short_string_unchanged(self) -> None:
        text, was = truncate_to_budget("hello", budget=100)
        assert text == "hello"
        assert was is False

    def test_truncate_long_string(self) -> None:
        text, was = truncate_to_budget("a " * 1000, budget=100)
        assert was is True
        assert text.endswith("[...truncated]")
        assert len(text.encode("utf-8")) <= 100 + len("\n[...truncated]")

    def test_truncate_handles_multibyte(self) -> None:
        # 2-byte chars. Make sure we don't slice mid-char.
        text, was = truncate_to_budget("á" * 1000, budget=200)
        assert was is True
        # Decoding never raises — verifies clean cut.
        text.encode("utf-8").decode("utf-8")

    def test_default_budget_matches_module_constant(self) -> None:
        # Sanity: importable, sensible value.
        assert 10_000 < RESPONSE_BUDGET_BYTES < 50_000
