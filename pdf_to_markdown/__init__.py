# -*- coding: utf-8 -*-
"""
pdf-to-markdown: High-performance PDF to structured Markdown converter.
"""

from .converter import (
    convert_pdf_to_markdown,
    batch_convert_pdf_directory,
    sanitize_filename,
    clean_page_text,
    build_adaptive_chapters,
)

__version__ = "1.0.0"
__all__ = [
    "convert_pdf_to_markdown",
    "batch_convert_pdf_directory",
    "sanitize_filename",
    "clean_page_text",
    "build_adaptive_chapters",
]
