"""
PDF → Markdown conversion module.

Strategy:
  1. Run opendataloader-pdf (Java JAR) to extract native text as markdown.
  2. Detect pages whose extracted text is too sparse (likely scanned/image).
  3. Re-render those pages with pdf2image and run Tesseract OCR via pytesseract.
  4. Splice OCR markdown back into the final output.

Dependencies for OCR fallback (install separately):
    pip install pytesseract pdf2image pillow
    # plus system packages: tesseract-ocr, poppler-utils
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .convert_generated import convert

# Minimum average characters per page expected from native extraction.
# Pages below this threshold are considered scanned and sent to Tesseract.
_SPARSE_THRESHOLD = 80


def _try_import_ocr() -> Tuple[bool, Optional[str]]:
    """Return (available, error_message)."""
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        return True, None
    except ImportError as exc:
        return False, str(exc)


def _extract_native_markdown(pdf_path: str, output_dir: str, **kwargs) -> str:
    """Run the opendataloader-pdf JAR and return markdown text."""
    convert(
        input_path=pdf_path,
        output_dir=output_dir,
        format="markdown",
        quiet=True,
        **kwargs,
    )
    stem = Path(pdf_path).stem
    md_path = Path(output_dir) / f"{stem}.md"
    if not md_path.exists():
        raise FileNotFoundError(f"Expected markdown output not found: {md_path}")
    return md_path.read_text(encoding="utf-8")


def _split_markdown_by_page(markdown: str, separator: str) -> List[str]:
    """Split markdown into per-page sections using the injected separator."""
    if not separator:
        return [markdown]
    parts = re.split(re.escape(separator), markdown)
    return [p.strip() for p in parts]


def _page_is_sparse(text: str) -> bool:
    """Return True if the page text has too few characters to be reliable."""
    return len(text.strip()) < _SPARSE_THRESHOLD


def _ocr_pages(pdf_path: str, page_numbers: List[int], lang: str, dpi: int) -> Dict[int, str]:
    """
    Render the given 1-based page numbers with pdf2image and OCR with Tesseract.
    Returns {page_number: markdown_text}.
    """
    import pdf2image
    import pytesseract

    results: Dict[int, str] = {}
    images = pdf2image.convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=min(page_numbers),
        last_page=max(page_numbers),
    )
    # pdf2image returns images from first_page onward; map back to original page numbers
    offset = min(page_numbers) - 1
    rendered: Dict[int, object] = {
        offset + i + 1: img for i, img in enumerate(images)
    }

    for page_num in page_numbers:
        img = rendered.get(page_num)
        if img is None:
            results[page_num] = ""
            continue
        raw_text = pytesseract.image_to_string(img, lang=lang)
        results[page_num] = _ocr_text_to_markdown(raw_text)

    return results


def _ocr_text_to_markdown(raw_text: str) -> str:
    """
    Light-weight heuristic conversion of raw Tesseract output to markdown.

    Rules applied (in order):
    - All-caps lines become ## headings.
    - Lines that look like numbered or bulleted list items are kept as-is.
    - Blank lines delimit paragraphs.
    - Multiple consecutive spaces are collapsed.
    """
    lines = raw_text.splitlines()
    md_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            md_lines.append("")
            continue

        # All-caps short line → section heading
        if stripped.isupper() and len(stripped) < 80 and not stripped[0].isdigit():
            md_lines.append(f"## {stripped.title()}")
            continue

        # Already looks like a markdown list item
        if re.match(r'^([-*•]|\d+[.)]) ', stripped):
            md_lines.append(stripped)
            continue

        # Collapse multiple spaces
        md_lines.append(re.sub(r' {2,}', ' ', stripped))

    # Collapse runs of 3+ blank lines down to 2
    result = re.sub(r'\n{3,}', '\n\n', "\n".join(md_lines))
    return result.strip()


def pdf_to_markdown(
    pdf_path: str,
    output_path: Optional[str] = None,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng",
    ocr_dpi: int = 300,
    sparse_threshold: int = _SPARSE_THRESHOLD,
    page_separator: str = "<!-- page {page} -->",
    **convert_kwargs,
) -> str:
    """
    Convert a PDF file to a markdown string.

    Uses the opendataloader-pdf Java JAR for native text extraction.  Pages
    with fewer than *sparse_threshold* characters are considered scanned and
    are re-processed with Tesseract OCR when *ocr_fallback* is True.

    Args:
        pdf_path: Path to the input PDF.
        output_path: Optional path to write the final markdown file.  If
            omitted the markdown is only returned as a string.
        ocr_fallback: Enable Tesseract OCR for sparse/scanned pages.
            Requires ``pytesseract``, ``pdf2image``, and ``Pillow``.
        ocr_lang: Tesseract language code(s), e.g. ``"eng"`` or ``"eng+fra"``.
        ocr_dpi: Resolution for pdf2image rendering (higher = better OCR,
            slower).  300 is a good default.
        sparse_threshold: Character count below which a page is considered
            scanned.  Default: 80.
        page_separator: Template string injected between pages in the native
            extraction pass.  The literal ``{page}`` is replaced with the
            1-based page number.  Set to ``""`` to disable page tracking
            (OCR fallback will then not be page-aware).
        **convert_kwargs: Extra keyword arguments forwarded to
            :func:`opendataloader_pdf.convert_generated.convert` (e.g.
            ``password``, ``pages``, ``keep_line_breaks``).

    Returns:
        The full markdown content as a string.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist or ``java`` is not
            on PATH.
        RuntimeError: If the Java conversion step fails and OCR is disabled.
    """
    pdf_path = str(Path(pdf_path).resolve())
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # We always inject page separators so we can detect sparse pages
    _sep_template = page_separator or ""
    # Build per-page separator that opendataloader-pdf understands:
    # it supports %page-number% in --markdown-page-separator
    odl_sep = _sep_template.replace("{page}", "%page-number%") if _sep_template else ""

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            native_md = _extract_native_markdown(
                pdf_path,
                tmp_dir,
                markdown_page_separator=odl_sep if odl_sep else None,
                **convert_kwargs,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            if not ocr_fallback:
                raise
            # Full OCR path when the JAR fails entirely
            native_md = ""

    # Split by separator to get per-page text
    if odl_sep:
        # Build a regex-friendly separator (strip %page-number% → match any digits)
        sep_regex = re.escape(_sep_template).replace(r"\{page\}", r"\d+")
        pages_text = re.split(sep_regex, native_md)
    else:
        pages_text = [native_md]

    # Identify pages that need OCR
    sparse_pages: List[int] = []
    if ocr_fallback:
        ocr_available, ocr_error = _try_import_ocr()
        if not ocr_available:
            import warnings
            warnings.warn(
                f"OCR fallback requested but dependencies missing: {ocr_error}. "
                "Install pytesseract, pdf2image, and Pillow to enable OCR. "
                "Proceeding with native extraction only.",
                ImportWarning,
                stacklevel=2,
            )
            ocr_fallback = False

    if ocr_fallback:
        for i, page_text in enumerate(pages_text):
            if _page_is_sparse(page_text) or len(page_text.strip()) < sparse_threshold:
                sparse_pages.append(i + 1)  # 1-based

    # Run OCR on sparse pages and splice results
    if sparse_pages:
        ocr_results = _ocr_pages(pdf_path, sparse_pages, lang=ocr_lang, dpi=ocr_dpi)
        for page_num, ocr_text in ocr_results.items():
            idx = page_num - 1
            if idx < len(pages_text):
                pages_text[idx] = ocr_text

    # Re-join with clean separators
    if len(pages_text) > 1:
        sections: List[str] = []
        for i, text in enumerate(pages_text):
            sep = _sep_template.replace("{page}", str(i + 1)) if _sep_template else ""
            if sep:
                sections.append(sep)
            if text.strip():
                sections.append(text.strip())
        final_markdown = "\n\n".join(sections)
    else:
        final_markdown = pages_text[0].strip()

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(final_markdown, encoding="utf-8")

    return final_markdown


def batch_pdf_to_markdown(
    pdf_dir: str,
    output_dir: str,
    pattern: str = "*.pdf",
    ocr_fallback: bool = True,
    ocr_lang: str = "eng",
    ocr_dpi: int = 300,
    sparse_threshold: int = _SPARSE_THRESHOLD,
    **convert_kwargs,
) -> Dict[str, str]:
    """
    Convert all PDFs in *pdf_dir* matching *pattern* to markdown files in
    *output_dir*.

    Returns a mapping of ``{input_path: output_path}`` for each converted file.
    Failed conversions are stored with an empty string value and a warning is
    printed to stderr.
    """
    import sys
    import warnings

    pdf_dir_path = Path(pdf_dir)
    out_dir_path = Path(output_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    results: Dict[str, str] = {}
    pdf_files = list(pdf_dir_path.glob(pattern))

    if not pdf_files:
        warnings.warn(f"No PDFs matching '{pattern}' found in {pdf_dir}", stacklevel=2)
        return results

    for pdf_file in pdf_files:
        out_file = out_dir_path / f"{pdf_file.stem}.md"
        try:
            pdf_to_markdown(
                str(pdf_file),
                output_path=str(out_file),
                ocr_fallback=ocr_fallback,
                ocr_lang=ocr_lang,
                ocr_dpi=ocr_dpi,
                sparse_threshold=sparse_threshold,
                **convert_kwargs,
            )
            results[str(pdf_file)] = str(out_file)
        except Exception as exc:
            print(f"[pdf_to_markdown] Failed to convert {pdf_file}: {exc}", file=sys.stderr)
            results[str(pdf_file)] = ""

    return results
