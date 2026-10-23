#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone execution entry point for pdf-to-markdown.
"""

import sys
from pathlib import Path

# Add project root to sys.path so it works directly without installation
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pdf_to_markdown.cli import main

if __name__ == "__main__":
    main()
