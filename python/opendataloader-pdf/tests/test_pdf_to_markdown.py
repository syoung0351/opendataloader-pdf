"""
Unit tests for pdf_to_markdown module.

All tests mock out the JAR runner and pytesseract so no real Java or Tesseract
installation is required to run the suite.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opendataloader_pdf.pdf_to_markdown import (
    _ocr_text_to_markdown,
    _page_is_sparse,
    _split_markdown_by_page,
    pdf_to_markdown,
)


# ---------------------------------------------------------------------------
# Pure-unit tests (no I/O)
# ---------------------------------------------------------------------------

class TestPageIsSparse:
    def test_empty_page_is_sparse(self):
        assert _page_is_sparse("") is True

    def test_whitespace_page_is_sparse(self):
        assert _page_is_sparse("   \n  ") is True

    def test_short_page_is_sparse(self):
        assert _page_is_sparse("Hello world.") is True

    def test_long_page_is_not_sparse(self):
        assert _page_is_sparse("a" * 200) is False


class TestSplitMarkdownByPage:
    def test_no_separator_returns_single_item(self):
        md = "# Title\n\nParagraph."
        result = _split_markdown_by_page(md, "")
        assert result == [md]

    def test_separator_splits_correctly(self):
        sep = "<!-- page -->"
        md = f"Page1 text{sep}Page2 text{sep}Page3 text"
        result = _split_markdown_by_page(md, sep)
        assert result == ["Page1 text", "Page2 text", "Page3 text"]


class TestOcrTextToMarkdown:
    def test_all_caps_becomes_heading(self):
        result = _ocr_text_to_markdown("INTRODUCTION")
        assert result.startswith("##")

    def test_list_item_preserved(self):
        result = _ocr_text_to_markdown("- item one")
        assert "- item one" in result

    def test_multiple_spaces_collapsed(self):
        result = _ocr_text_to_markdown("hello   world")
        assert "hello world" in result

    def test_blank_lines_collapsed(self):
        raw = "line1\n\n\n\n\nline2"
        result = _ocr_text_to_markdown(raw)
        assert "\n\n\n" not in result


# ---------------------------------------------------------------------------
# Integration-style tests (mock JAR + pytesseract)
# ---------------------------------------------------------------------------

NATIVE_MARKDOWN = textwrap.dedent("""\
    # Document Title

    This is the first paragraph with enough text to exceed the sparse threshold.
    Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod.

    <!-- page 2 -->

    tiny

    <!-- page 3 -->

    Third page content that is also long enough to not trigger OCR fallback.
    More text here to ensure it clears the threshold with plenty of margin.
""")


def _make_pdf_file(tmp_path: Path) -> Path:
    """Create a dummy PDF file (content doesn't matter; JAR is mocked)."""
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


@patch("opendataloader_pdf.pdf_to_markdown._ocr_pages")
@patch("opendataloader_pdf.pdf_to_markdown._extract_native_markdown")
def test_pdf_to_markdown_no_ocr_needed(mock_extract, mock_ocr, tmp_path):
    """All pages dense enough → OCR not triggered."""
    dense_md = "a" * 300 + "\n\n<!-- page 2 -->\n\n" + "b" * 300
    mock_extract.return_value = dense_md

    pdf_file = _make_pdf_file(tmp_path)
    result = pdf_to_markdown(str(pdf_file), page_separator="<!-- page {page} -->")

    mock_ocr.assert_not_called()
    assert "aaa" in result


@patch("opendataloader_pdf.pdf_to_markdown._ocr_pages")
@patch("opendataloader_pdf.pdf_to_markdown._extract_native_markdown")
def test_pdf_to_markdown_ocr_triggered_for_sparse_page(mock_extract, mock_ocr, tmp_path):
    """Sparse page 2 should be sent to OCR."""
    mock_extract.return_value = NATIVE_MARKDOWN
    mock_ocr.return_value = {2: "## Ocr Heading\n\nOCR content for page two."}

    pdf_file = _make_pdf_file(tmp_path)
    result = pdf_to_markdown(str(pdf_file), page_separator="<!-- page {page} -->")

    mock_ocr.assert_called_once()
    called_pages = mock_ocr.call_args[0][1]  # second positional arg
    assert 2 in called_pages
    assert "OCR content for page two" in result


@patch("opendataloader_pdf.pdf_to_markdown._extract_native_markdown")
def test_pdf_to_markdown_writes_output_file(mock_extract, tmp_path):
    """output_path kwarg causes file to be written."""
    mock_extract.return_value = "# Hello\n\n" + "x" * 200

    pdf_file = _make_pdf_file(tmp_path)
    out_file = tmp_path / "out" / "result.md"
    pdf_to_markdown(
        str(pdf_file),
        output_path=str(out_file),
        ocr_fallback=False,
        page_separator="",
    )

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "Hello" in content


def test_pdf_to_markdown_raises_for_missing_pdf(tmp_path):
    """Non-existent PDF raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        pdf_to_markdown(str(tmp_path / "no_such.pdf"))
