# pdf-to-markdown

[![PyPI version](https://img.shields.io/badge/pypi-v1.0.0-blue.svg)](https://pypi.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.9+](https://img.shields.io/badge/python-3.9+-brightgreen.svg)](https://www.python.org/)

**High-performance PDF to structured Markdown converter** with adaptive TOC bookmark partitioning, physical page anchors, watermark scrubbing, and SQLite FTS5 full-text indexing.

高性能 PDF 结构化转 Markdown 工具：专为超长技术手册、规范标准、规程文档及大模型 RAG 数据清洗设计。支持自适应书签递归分卷、物理页码保留、水印智能清洗与 SQLite FTS5 全文检索引擎。

---

## Key Features / 核心特性

- **Adaptive Bookmark Partitioning / 自适应书签递归分卷**：
  Automatically parses native PDF bookmark trees (TOC). If a section exceeds the threshold (default: 400 pages), it dynamically drills down into child bookmarks. For PDFs without bookmarks, it falls back to smooth chunking. 100% gapless, non-overlapping coverage from page 1 to the end.
- **Physical Page Anchors / 物理页码高保真保留**：
  Injects `### [P.xxx]` anchors on every page and annotates native bookmarks for each page, preserving exact citation tracking for RAG and legal compliance.
- **Watermark Scrubbing / 水印与冗余行清洗**：
  Built-in regex filters for internal distribution watermarks (such as employee IDs or confidentiality markings) and consecutive blank lines.
- **Batch Multi-Processing / 批量多进程并发**：
  Scales across multiple CPU cores. Automatically processes largest files first to eliminate long tails. Converts tens of thousands of pages in seconds.
- **Full-Text Search Engine / 内置 SQLite FTS5 全文索引**：
  Optionally compiles all extracted pages directly into a local SQLite FTS5 database (`--build-fts5`) for millisecond-latency BM25 lexical search.
- **Single-File or Modular Modes / 单文件与分卷双模式**：
  Supports `--single-file` for research papers or short reports, as well as modular subdirectories for enterprise manuals.

---

## Installation / 安装

### Via Pip (Local or Repository)
```bash
git clone https://github.com/xair330/pdf-to-markdown.git
cd pdf-to-markdown
pip install -e .
```

### Dependencies
- Python 3.9+
- `pymupdf>=1.23.0`

```bash
pip install -r requirements.txt
```

---

## CLI Usage / 命令行使用

### 1. Convert Single PDF by Chapters / 单文件按章节分卷
```bash
pdf-to-markdown path/to/manual.pdf -o output_dir
```

### 2. Convert Single PDF as Unified Markdown / 单文件整体导出
For academic papers, research reports, or short articles:
```bash
pdf-to-markdown path/to/paper.pdf -o output_dir --single-file
```

### 3. Batch Directory Processing / 批量多进程转换
Convert hundreds of PDFs in parallel with 8 workers and build an SQLite FTS5 index:
```bash
pdf-to-markdown path/to/pdf_folder -o output_dir -w 8 --build-fts5
```

### CLI Options / 参数说明

| Parameter | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `input_path` | Positional | Required | Path to input PDF file or directory containing PDFs |
| `-o`, `--output-dir` | Flag | Required | Output directory for generated Markdown files |
| `-w`, `--workers` | Flag | `4` | Number of worker processes for batch processing |
| `--max-chapter-pages`| Flag | `400` | Max page count per chapter before unrolling child TOC |
| `--chunk-size` | Flag | `100` | Fallback chunk size (pages) for PDFs without bookmarks |
| `--single-file` | Switch | `False` | Force output into a single `.md` file |
| `--build-fts5` | Switch | `False` | Build SQLite FTS5 full-text search database |
| `--watermark-regex` | Flag | `None` | Append custom regex pattern to scrub |

---

## Python API / 代码直接调用

```python
from pdf_to_markdown import convert_pdf_to_markdown, batch_convert_pdf_directory

# 1. Convert single PDF
result = convert_pdf_to_markdown(
    pdf_path="documents/manual.pdf",
    output_dir="output_markdown",
    max_chapter_pages=300,
    single_file=False
)
print(f"Total pages: {result['total_pages']}, Chapters: {result['chapter_count']}")

# 2. Batch convert directory with FTS5
batch_result = batch_convert_pdf_directory(
    input_dir="documents/all_pdfs",
    output_dir="output_markdown",
    workers=4,
    build_fts5=True
)
print(f"Converted {batch_result['total_pdfs']} PDFs in {batch_result['total_elapsed_sec']}s")
```

---

## Output Structure / 输出目录结构

```text
output_dir/
├── _TOC_Index.md                   # Master index (batch mode)
├── manuals_fts5.sqlite             # SQLite FTS5 search database (optional)
└── Manual_Name/
    ├── 00_TOC_Index.md             # Manual-level chapter index & outline
    ├── 01_Chapter_Title_P0001-P0080.md
    ├── 02_Chapter_Title_P0081-P0210.md
    └── ...
```

Each generated Markdown file contains structured YAML frontmatter:
```markdown
---
manual_name: "Flight_Crew_Operating_Manual.pdf"
chapter_index: 2
chapter_title: "Aircraft Systems"
start_page: 81
end_page: 210
page_count: 130
source_pdf: "/absolute/path/Flight_Crew_Operating_Manual.pdf"
---

# Flight_Crew_Operating_Manual.pdf — Aircraft Systems (P.81–P.210)

### [P.81]
> **Bookmarks**: `[L1] Systems` / `[L2] Electrical`

System overview text...
```

---

## SQLite FTS5 Search Example / 全文检索查询示例

When `--build-fts5` is enabled, you can perform instant lexical queries across thousands of pages:

```python
import sqlite3

conn = sqlite3.connect("output_dir/manuals_fts5.sqlite")
cur = conn.cursor()

query = "hydraulic leak OR emergency landing"
cur.execute("""
    SELECT manual_name, chapter_title, page_num, md_rel_path, snippet(pages_fts, 4, '<b>', '</b>', '...', 15)
    FROM pages_fts
    WHERE pages_fts MATCH ?
    ORDER BY rank
    LIMIT 5;
""", (query,))

for row in cur.fetchall():
    print(f"[{row[0]} P.{row[2]}] in {row[1]}: {row[4]}")
```

---

## License / 开源协议

Released under the [MIT License](LICENSE). Copyright (c) 2026 xair330.
