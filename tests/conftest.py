"""Test fixtures. Builds PDFs once per session using reportlab."""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _build_simple_pdf(path: Path) -> None:
    """Three-page PDF with distinctive, searchable per-page content."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setTitle("Simple Test Document")
    c.setAuthor("mcp-pdf-extract tests")
    c.setSubject("Fixture for unit tests")

    page_contents = [
        ("First page header", "The quick brown fox jumps over the lazy dog."),
        ("Second page header", "Lorem ipsum dolor sit amet, consectetur adipiscing elit."),
        ("Third page header", "Searchable_marker appears on this page exactly once."),
    ]
    for i, (header, body) in enumerate(page_contents, start=1):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 720, header)
        c.setFont("Helvetica", 12)
        c.drawString(72, 680, body)
        c.drawString(72, 660, f"Page number: {i}")
        c.showPage()
    c.save()


def _build_table_pdf(path: Path) -> None:
    """Single-page PDF containing a simple 3-column table."""
    doc = SimpleDocTemplate(str(path), pagesize=LETTER)
    data = [
        ["Name", "Quantity", "Price"],
        ["Apple", "10", "1.20"],
        ["Banana", "5", "0.50"],
        ["Cherry", "20", "3.00"],
    ]
    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    doc.build([table])


def _build_large_pdf(path: Path) -> None:
    """PDF with ~80 pages of dense text to exercise the 40KB budget."""
    c = canvas.Canvas(str(path), pagesize=LETTER)
    body_line = "The quick brown fox jumps over the lazy dog. " * 12
    for i in range(1, 81):
        c.setFont("Helvetica", 10)
        c.drawString(36, 760, f"Page {i}")
        y = 730
        for _ in range(50):
            c.drawString(36, y, body_line)
            y -= 12
            if y < 36:
                break
        c.showPage()
    c.save()


@pytest.fixture(scope="session")
def simple_pdf() -> Path:
    path = FIXTURE_DIR / "simple.pdf"
    FIXTURE_DIR.mkdir(exist_ok=True)
    if not path.exists():
        _build_simple_pdf(path)
    return path


@pytest.fixture(scope="session")
def table_pdf() -> Path:
    path = FIXTURE_DIR / "with_table.pdf"
    FIXTURE_DIR.mkdir(exist_ok=True)
    if not path.exists():
        _build_table_pdf(path)
    return path


@pytest.fixture(scope="session")
def large_pdf() -> Path:
    path = FIXTURE_DIR / "large.pdf"
    FIXTURE_DIR.mkdir(exist_ok=True)
    if not path.exists():
        _build_large_pdf(path)
    return path
