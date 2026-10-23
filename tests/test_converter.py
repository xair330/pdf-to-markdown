# -*- coding: utf-8 -*-
"""
Tests for pdf_to_markdown core functions.
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from pdf_to_markdown.converter import (
    sanitize_filename,
    clean_page_text,
    build_adaptive_chapters,
)


class TestPdfToMarkdown(unittest.TestCase):
    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename('Normal_File.pdf'), 'Normal_File.pdf')
        self.assertEqual(sanitize_filename('File/With:Invalid*Chars?'), 'File_With_Invalid_Chars')
        self.assertTrue(len(sanitize_filename('A' * 100, max_len=50)) <= 50)

    def test_clean_page_text(self):
        import re
        watermark_res = [re.compile(r"^\s*\d{6}\s+[\u4e00-\u9fa5]{2,4}\s*$")]
        raw = "197238 张三\nLine 1\n\n\n\nLine 2\n"
        cleaned = clean_page_text(raw, watermark_res)
        self.assertNotIn("197238", cleaned)
        self.assertIn("Line 1", cleaned)
        self.assertIn("Line 2", cleaned)
        self.assertNotIn("\n\n\n", cleaned)

    def test_build_adaptive_chapters_no_toc(self):
        chapters = build_adaptive_chapters([], total_pages=250, max_chapter_pages=100, default_chunk_size=100)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0]["start_page"], 1)
        self.assertEqual(chapters[0]["end_page"], 100)
        self.assertEqual(chapters[1]["start_page"], 101)
        self.assertEqual(chapters[1]["end_page"], 200)
        self.assertEqual(chapters[2]["start_page"], 201)
        self.assertEqual(chapters[2]["end_page"], 250)


if __name__ == "__main__":
    unittest.main()
