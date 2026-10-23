# -*- coding: utf-8 -*-
"""
pdf_to_markdown.converter - Core engine for adaptive PDF parsing and Markdown export.
"""

import os
import re
import sys
import time
import json
import sqlite3
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Default regex patterns for scrubbing employee ID watermarks and confidential markers
DEFAULT_WATERMARK_PATTERNS = [
    r"^\s*\d{6}\s+[\u4e00-\u9fa5]{2,4}\s*$",  # e.g., "197238 王旺"
    r"^\s*机密\s*·\s*内部资料\s*$",
    r"^\s*CONFIDENTIAL\s*$",
]

MULTI_BLANK_RE = re.compile(r"\n{3,}")


def sanitize_filename(name: str, max_len: int = 70) -> str:
    """Clean filename of illegal filesystem characters across Windows/macOS/Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    if not cleaned:
        cleaned = "Section"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("._ ")
    return cleaned


def clean_page_text(raw_text: str, watermark_res: list) -> str:
    """Clean single page text by stripping watermarks and normalizing blanks."""
    if not raw_text:
        return ""
    lines = []
    for line in raw_text.splitlines():
        is_watermark = False
        for w_re in watermark_res:
            if w_re.match(line):
                is_watermark = True
                break
        if not is_watermark:
            lines.append(line.rstrip())
    joined = "\n".join(lines).strip()
    joined = MULTI_BLANK_RE.sub("\n\n", joined)
    return joined


def build_adaptive_chapters(
    toc: list,
    total_pages: int,
    max_chapter_pages: int = 400,
    default_chunk_size: int = 100
) -> list:
    """
    Build adaptive non-overlapping chapters covering [1, total_pages] gaplessly.
    Drills down to child bookmarks if a chapter exceeds max_chapter_pages.
    """
    if total_pages <= 0:
        return []

    # Normalize bookmark table
    norm_toc = []
    for item in toc:
        if len(item) >= 3:
            lvl, title, p = int(item[0]), str(item[1]).strip(), int(item[2])
            p = max(1, min(total_pages, p))
            title = re.sub(r"\s+", " ", title) or f"Page_{p}"
            norm_toc.append({"level": lvl, "title": title, "page": p})

    # Fallback for PDFs without bookmarks
    if not norm_toc:
        if total_pages <= max_chapter_pages:
            return [{"title": "Full_Content", "start_page": 1, "end_page": total_pages, "level": 1}]
        chunks = []
        for sp in range(1, total_pages + 1, default_chunk_size):
            ep = min(total_pages, sp + default_chunk_size - 1)
            chunks.append({
                "title": f"Part_P{sp:04d}_P{ep:04d}",
                "start_page": sp,
                "end_page": ep,
                "level": 1,
            })
        return chunks

    # Build hierarchical tree
    root = {"level": 0, "title": "ROOT", "page": 1, "children": []}
    stack = [root]
    for node in norm_toc:
        new_node = {
            "level": node["level"],
            "title": node["title"],
            "page": node["page"],
            "children": [],
        }
        while len(stack) > 1 and stack[-1]["level"] >= new_node["level"]:
            stack.pop()
        stack[-1]["children"].append(new_node)
        stack.append(new_node)

    def assign_ranges(children: list, parent_end: int):
        for i, ch in enumerate(children):
            sp = ch["page"]
            if i + 1 < len(children):
                next_sp = children[i + 1]["page"]
                ep = max(sp, next_sp - 1) if next_sp > sp else sp
            else:
                ep = parent_end
            ep = max(sp, min(parent_end, ep))
            ch["start_page"] = sp
            ch["end_page"] = ep
            if ch["children"]:
                assign_ranges(ch["children"], ep)

    assign_ranges(root["children"], total_pages)

    # Recursively unroll oversized sections (up to level 3)
    raw_sections = []

    def collect_sections(nodes: list, prefix: str = "", depth: int = 1):
        for n in nodes:
            full_title = f"{prefix} - {n['title']}" if prefix else n["title"]
            span = n["end_page"] - n["start_page"] + 1
            should_drill = (
                n["children"]
                and depth < 3
                and (span > 250 or (depth == 1 and len(nodes) <= 3 and span > 40))
            )
            if should_drill:
                first_child_p = n["children"][0]["page"]
                if first_child_p > n["start_page"]:
                    raw_sections.append({
                        "title": f"{full_title} (Overview)",
                        "start_page": n["start_page"],
                        "end_page": first_child_p - 1,
                        "level": n["level"],
                    })
                collect_sections(n["children"], prefix=full_title, depth=depth + 1)
            else:
                raw_sections.append({
                    "title": full_title,
                    "start_page": n["start_page"],
                    "end_page": n["end_page"],
                    "level": n["level"],
                })

    collect_sections(root["children"], prefix="", depth=1)

    # Enforce strictly monotonic increasing boundaries
    valid_starts = []
    for sec in raw_sections:
        sp = max(1, min(total_pages, sec["start_page"]))
        if not valid_starts:
            if sp > 1:
                valid_starts.append({"title": "Front_Matter", "start_page": 1, "level": 1})
            valid_starts.append({"title": sec["title"], "start_page": sp, "level": sec["level"]})
        else:
            prev_sp = valid_starts[-1]["start_page"]
            if sp > prev_sp:
                valid_starts.append({"title": sec["title"], "start_page": sp, "level": sec["level"]})
            elif sp == prev_sp:
                if sec["title"] not in valid_starts[-1]["title"] and len(valid_starts[-1]["title"]) < 80:
                    valid_starts[-1]["title"] += f" / {sec['title']}"

    if not valid_starts:
        valid_starts = [{"title": "Full_Content", "start_page": 1, "level": 1}]

    final_chapters = []
    for i, item in enumerate(valid_starts):
        sp = item["start_page"]
        ep = valid_starts[i + 1]["start_page"] - 1 if i + 1 < len(valid_starts) else total_pages
        if ep < sp:
            continue
        span = ep - sp + 1
        if span > max_chapter_pages:
            part_idx = 1
            chunk_step = max_chapter_pages // 2
            for sub_sp in range(sp, ep + 1, chunk_step):
                sub_ep = min(ep, sub_sp + chunk_step - 1)
                final_chapters.append({
                    "title": f"{item['title']} (Part {part_idx}: P.{sub_sp}-P.{sub_ep})",
                    "start_page": sub_sp,
                    "end_page": sub_ep,
                    "level": item["level"],
                })
                part_idx += 1
        else:
            final_chapters.append({
                "title": item["title"],
                "start_page": sp,
                "end_page": ep,
                "level": item["level"],
            })

    return final_chapters


def convert_pdf_to_markdown(
    pdf_path: str,
    output_dir: str,
    custom_watermark_patterns: list = None,
    max_chapter_pages: int = 400,
    default_chunk_size: int = 100,
    single_file: bool = False,
    page_cache_writer = None,
) -> dict:
    """
    Convert a single PDF file into structured Markdown files.
    """
    if fitz is None:
        raise ImportError("PyMuPDF is not installed. Please run `pip install pymupdf`.")

    t0 = time.time()
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    filename = os.path.basename(pdf_path)
    stem = os.path.splitext(filename)[0]

    patterns = list(DEFAULT_WATERMARK_PATTERNS)
    if custom_watermark_patterns:
        patterns.extend(custom_watermark_patterns)
    watermark_res = [re.compile(p) for p in patterns]

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    raw_toc = doc.get_toc()

    # Map page number to bookmarks
    page_bookmarks = {}
    for b in raw_toc:
        if len(b) >= 3:
            lvl, btitle, bpage = int(b[0]), str(b[1]).strip(), int(b[2])
            bpage = max(1, min(total_pages, bpage))
            page_bookmarks.setdefault(bpage, []).append((lvl, re.sub(r"\s+", " ", btitle)))

    os.makedirs(output_dir, exist_ok=True)

    # Single-file mode
    if single_file or (total_pages <= 30 and not raw_toc):
        out_md_path = os.path.join(output_dir, f"{sanitize_filename(stem)}.md")
        md_lines = [
            "---",
            f'title: "{stem.replace(chr(34), chr(39))}"',
            f"total_pages: {total_pages}",
            f'source_pdf: "{os.path.abspath(pdf_path).replace(chr(92), "/")}"',
            "---",
            "",
            f"# {stem}",
            "",
        ]
        non_empty = 0
        total_chars = 0
        for pno in range(1, total_pages + 1):
            txt = clean_page_text(doc[pno - 1].get_text("text"), watermark_res)
            if txt:
                non_empty += 1
                total_chars += len(txt)
            md_lines.append(f"### [P.{pno}]")
            if pno in page_bookmarks:
                b_strs = [f"`[L{blvl}] {bt}`" for blvl, bt in page_bookmarks[pno][:6]]
                md_lines.append(f"> **Bookmarks**: {' / '.join(b_strs)}")
            md_lines.append("")
            md_lines.append(txt if txt else "*(No text extracted)*")
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

            if page_cache_writer:
                page_cache_writer({
                    "manual_name": filename,
                    "chapter_title": "Full",
                    "page_num": pno,
                    "md_rel_path": os.path.basename(out_md_path),
                    "content": txt,
                })

        with open(out_md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
        doc.close()
        return {
            "filename": filename,
            "total_pages": total_pages,
            "non_empty_pages": non_empty,
            "total_chars": total_chars,
            "chapter_count": 1,
            "output_path": out_md_path,
            "mode": "SINGLE_FILE",
            "elapsed_sec": round(time.time() - t0, 2),
        }

    # Chapter-split mode
    manual_sub_dir = os.path.join(output_dir, sanitize_filename(stem, max_len=80))
    os.makedirs(manual_sub_dir, exist_ok=True)

    chapters = build_adaptive_chapters(
        raw_toc, total_pages, max_chapter_pages=max_chapter_pages, default_chunk_size=default_chunk_size
    )

    chapter_records = []
    total_chars = 0
    non_empty_pages = 0

    for idx, ch in enumerate(chapters, start=1):
        sp, ep = ch["start_page"], ch["end_page"]
        ch_title = ch["title"]
        ch_filename = f"{idx:02d}_{sanitize_filename(ch_title, max_len=60)}_P{sp:04d}-P{ep:04d}.md"
        ch_path = os.path.join(manual_sub_dir, ch_filename)

        md_lines = [
            "---",
            f'manual_name: "{filename}"',
            f"chapter_index: {idx}",
            f'chapter_title: "{ch_title.replace(chr(34), chr(39))}"',
            f"start_page: {sp}",
            f"end_page: {ep}",
            f"page_count: {ep - sp + 1}",
            f'source_pdf: "{os.path.abspath(pdf_path).replace(chr(92), "/")}"',
            "---",
            "",
            f"# {filename} — {ch_title} (P.{sp}–P.{ep})",
            "",
            f"> TOC: [00_TOC_Index.md](./00_TOC_Index.md) | Pages: `P.{sp}` – `P.{ep}`",
            "",
        ]

        ch_chars = 0
        for pno in range(sp, ep + 1):
            txt = clean_page_text(doc[pno - 1].get_text("text"), watermark_res)
            if txt:
                non_empty_pages += 1
                ch_chars += len(txt)
                total_chars += len(txt)

            md_lines.append(f"### [P.{pno}]")
            if pno in page_bookmarks:
                b_strs = [f"`[L{blvl}] {bt}`" for blvl, bt in page_bookmarks[pno][:8]]
                md_lines.append(f"> **Bookmarks**: {' / '.join(b_strs)}")
            md_lines.append("")
            md_lines.append(txt if txt else "*(Image, diagram, or blank separator page)*")
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

            if page_cache_writer:
                page_cache_writer({
                    "manual_name": filename,
                    "chapter_title": ch_title,
                    "page_num": pno,
                    "md_rel_path": os.path.relpath(ch_path, output_dir).replace("\\", "/"),
                    "content": txt,
                })

        with open(ch_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        chapter_records.append({
            "chapter_index": idx,
            "chapter_title": ch_title,
            "start_page": sp,
            "end_page": ep,
            "page_count": ep - sp + 1,
            "char_count": ch_chars,
            "md_filename": ch_filename,
            "md_path": ch_path,
        })

    doc.close()

    # Generate TOC file
    toc_md_path = os.path.join(manual_sub_dir, "00_TOC_Index.md")
    toc_lines = [
        "---",
        f'manual_name: "{filename}"',
        f"total_pages: {total_pages}",
        f"total_chapters: {len(chapter_records)}",
        f"native_bookmark_count: {len(raw_toc)}",
        f'source_pdf: "{os.path.abspath(pdf_path).replace(chr(92), "/")}"',
        "---",
        "",
        f"# {filename} — Table of Contents",
        "",
        f"- **Source PDF**: `{os.path.abspath(pdf_path)}`",
        f"- **Total Pages**: `{total_pages:,}` (Text pages: `{non_empty_pages:,}`, Characters: `{total_chars:,}`)",
        f"- **Chapters**: `{len(chapter_records)}` files (Gapless coverage `P.1` – `P.{total_pages}`)",
        "",
        "## Chapters Overview",
        "",
        "| Index | Chapter Title | Page Range | Pages | Characters | Markdown Link |",
        "| :---: | :--- | :---: | :---: | :---: | :--- |",
    ]
    for cr in chapter_records:
        toc_lines.append(
            f"| `{cr['chapter_index']:02d}` | {cr['chapter_title'].replace('|', '/')} | `P.{cr['start_page']}–P.{cr['end_page']}` | {cr['page_count']} | {cr['char_count']:,} | [{cr['md_filename']}](./{cr['md_filename']}) |"
        )

    if raw_toc:
        toc_lines.extend([
            "",
            "## PDF Bookmark Outline (Top 200)",
            "",
            "| Level | Title | Page |",
            "| :---: | :--- | :---: |",
        ])
        for b in raw_toc[:200]:
            if len(b) >= 3:
                lvl, btitle, bpage = int(b[0]), str(b[1]).strip().replace("|", "/"), int(b[2])
                indent = "&emsp;" * max(0, lvl - 1)
                toc_lines.append(f"| `L{lvl}` | {indent}{btitle} | `P.{bpage}` |")

    with open(toc_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(toc_lines))

    return {
        "filename": filename,
        "total_pages": total_pages,
        "non_empty_pages": non_empty_pages,
        "total_chars": total_chars,
        "chapter_count": len(chapter_records),
        "manual_sub_dir": manual_sub_dir,
        "toc_md_path": toc_md_path,
        "mode": "CHAPTER_SPLIT",
        "elapsed_sec": round(time.time() - t0, 2),
    }


def _worker_convert_task(task_kwargs):
    """Worker task wrapper for ProcessPoolExecutor."""
    pdf_path = task_kwargs["pdf_path"]
    out_dir = task_kwargs["output_dir"]
    cache_jsonl = task_kwargs.get("cache_jsonl")

    cache_file = None
    cache_writer = None
    if cache_jsonl:
        cache_file = open(cache_jsonl, "w", encoding="utf-8")
        def _writer(page_dict):
            cache_file.write(json.dumps(page_dict, ensure_ascii=False) + "\n")
        cache_writer = _writer

    try:
        res = convert_pdf_to_markdown(
            pdf_path=pdf_path,
            output_dir=out_dir,
            custom_watermark_patterns=task_kwargs.get("custom_watermarks"),
            max_chapter_pages=task_kwargs.get("max_chapter_pages", 400),
            default_chunk_size=task_kwargs.get("default_chunk_size", 100),
            single_file=task_kwargs.get("single_file", False),
            page_cache_writer=cache_writer,
        )
        res["cache_jsonl"] = cache_jsonl
        return res
    finally:
        if cache_file:
            cache_file.close()


def batch_convert_pdf_directory(
    input_dir: str,
    output_dir: str,
    workers: int = 4,
    build_fts5: bool = False,
    max_chapter_pages: int = 400,
    custom_watermarks: list = None,
) -> dict:
    """
    Batch convert all PDF files in a directory to Markdown with multi-processing.
    """
    start_time = time.time()
    os.makedirs(output_dir, exist_ok=True)

    pdf_files = []
    for root, _, files in os.walk(input_dir):
        for f in sorted(files):
            if f.lower().endswith(".pdf"):
                full_p = os.path.join(root, f)
                pdf_files.append(full_p)

    if not pdf_files:
        print(f"No .pdf files found in directory: {input_dir}")
        return {"total_pdfs": 0}

    print(f"[*] Found {len(pdf_files)} PDF files. Launching {workers} workers...")

    temp_cache_dir = os.path.join(output_dir, ".temp_page_cache")
    if build_fts5:
        os.makedirs(temp_cache_dir, exist_ok=True)

    tasks = []
    for idx, p in enumerate(pdf_files):
        c_jsonl = os.path.join(temp_cache_dir, f"cache_{idx:04d}.jsonl") if build_fts5 else None
        tasks.append({
            "pdf_path": p,
            "output_dir": output_dir,
            "custom_watermarks": custom_watermarks,
            "max_chapter_pages": max_chapter_pages,
            "cache_jsonl": c_jsonl,
        })

    # Sort largest files first to eliminate long tails
    tasks.sort(key=lambda t: os.path.getsize(t["pdf_path"]), reverse=True)

    results = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker_convert_task, t): t for t in tasks}
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            done += 1
            print(f"  [{done}/{len(tasks)}] Done: {res['filename']} ({res['total_pages']} pages in {res['elapsed_sec']}s)")

    results.sort(key=lambda r: r["filename"])

    grand_pages = sum(r["total_pages"] for r in results)
    grand_chars = sum(r["total_chars"] for r in results)
    grand_chapters = sum(r.get("chapter_count", 1) for r in results)

    # Master index
    index_md_path = os.path.join(output_dir, "_TOC_Index.md")
    idx_lines = [
        "# Knowledge Base Markdown Index",
        "",
        f"- **Generated**: `{datetime.now(timezone.utc).isoformat()}`",
        f"- **Source Directory**: `{os.path.abspath(input_dir)}`",
        f"- **PDF Total**: `{len(results):,}`",
        f"- **Pages Total**: `{grand_pages:,}`",
        f"- **Chapters Total**: `{grand_chapters:,}`",
        f"- **Characters Total**: `{grand_chars:,}`",
        "",
        "## Documents List",
        "",
        "| No. | File Name | Pages | Chapters | Characters | TOC Link |",
        "| :---: | :--- | :---: | :---: | :---: | :--- |",
    ]
    for i, r in enumerate(results, start=1):
        if r["mode"] == "SINGLE_FILE":
            rel_link = os.path.basename(r["output_path"])
        else:
            rel_link = os.path.relpath(r["toc_md_path"], output_dir).replace("\\", "/")
        idx_lines.append(
            f"| `{i:02d}` | `{r['filename']}` | {r['total_pages']:,} | {r.get('chapter_count', 1)} | {r['total_chars']:,} | [TOC](./{rel_link}) |"
        )

    with open(index_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(idx_lines))

    # Optional SQLite FTS5 database
    db_path = None
    if build_fts5:
        db_path = os.path.join(output_dir, "manuals_fts5.sqlite")
        if os.path.exists(db_path):
            os.remove(db_path)
        print(f"[*] Building SQLite FTS5 full-text index database: {db_path}...")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA synchronous = OFF;")
        cur.execute("PRAGMA journal_mode = MEMORY;")
        cur.execute("""
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                manual_name,
                chapter_title,
                page_num UNINDEXED,
                md_rel_path UNINDEXED,
                content,
                tokenize='unicode61'
            );
        """)
        conn.execute("BEGIN TRANSACTION;")
        for r in results:
            c_jsonl = r.get("cache_jsonl")
            if c_jsonl and os.path.exists(c_jsonl):
                rows = []
                with open(c_jsonl, "r", encoding="utf-8") as cf:
                    for line in cf:
                        p_data = json.loads(line)
                        rows.append((
                            p_data["manual_name"],
                            p_data["chapter_title"],
                            p_data["page_num"],
                            p_data["md_rel_path"],
                            p_data["content"],
                        ))
                cur.executemany("INSERT INTO pages_fts VALUES (?, ?, ?, ?, ?)", rows)
                try:
                    os.remove(c_jsonl)
                except OSError:
                    pass
        conn.commit()
        conn.close()
        try:
            os.rmdir(temp_cache_dir)
        except OSError:
            pass

    total_elapsed = round(time.time() - start_time, 2)
    print(f"[✓] Batch conversion complete: {len(results)} PDFs, {grand_pages:,} pages in {total_elapsed}s.")
    if db_path:
        print(f"[✓] SQLite FTS5 index ready: {db_path}")

    return {
        "total_pdfs": len(results),
        "total_pages": grand_pages,
        "total_chapters": grand_chapters,
        "total_chars": grand_chars,
        "total_elapsed_sec": total_elapsed,
        "index_md_path": index_md_path,
        "sqlite_fts5_path": db_path,
    }
