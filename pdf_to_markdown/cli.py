# -*- coding: utf-8 -*-
"""
pdf_to_markdown.cli - Command-line interface for pdf-to-markdown.
"""

import os
import sys
import argparse
from .converter import convert_pdf_to_markdown, batch_convert_pdf_directory


def main():
    parser = argparse.ArgumentParser(
        prog="pdf-to-markdown",
        description="High-performance PDF to structured Markdown converter with adaptive TOC partitioning and physical page anchors."
    )
    parser.add_argument("input_path", type=str, help="Input PDF file or directory containing PDF files")
    parser.add_argument("-o", "--output-dir", type=str, required=True, help="Output directory for generated Markdown")
    parser.add_argument("-w", "--workers", type=int, default=4, help="Worker processes for batch conversion (default: 4)")
    parser.add_argument("--max-chapter-pages", type=int, default=400, help="Max pages per chapter before unrolling child TOC (default: 400)")
    parser.add_argument("--chunk-size", type=int, default=100, help="Fallback chunk size in pages for PDFs without TOC (default: 100)")
    parser.add_argument("--single-file", action="store_true", help="Force output as a single unified Markdown file")
    parser.add_argument("--build-fts5", action="store_true", help="Build SQLite FTS5 full-text search database alongside Markdown")
    parser.add_argument("--watermark-regex", type=str, action="append", help="Additional custom watermark regex to scrub")

    args = parser.parse_args()

    inp = os.path.abspath(args.input_path)
    out = os.path.abspath(args.output_dir)

    if os.path.isfile(inp):
        print(f"[*] Processing single PDF: {inp} -> {out}")
        res = convert_pdf_to_markdown(
            pdf_path=inp,
            output_dir=out,
            custom_watermark_patterns=args.watermark_regex,
            max_chapter_pages=args.max_chapter_pages,
            default_chunk_size=args.chunk_size,
            single_file=args.single_file,
        )
        print(f"[✓] Converted successfully! Pages: {res['total_pages']}, Chars: {res['total_chars']}, Time: {res['elapsed_sec']}s")
    elif os.path.isdir(inp):
        batch_convert_pdf_directory(
            input_dir=inp,
            output_dir=out,
            workers=args.workers,
            build_fts5=args.build_fts5,
            max_chapter_pages=args.max_chapter_pages,
            custom_watermarks=args.watermark_regex,
        )
    else:
        print(f"Error: input path does not exist: {inp}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
