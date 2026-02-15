#!/usr/bin/env python3
"""
Ebook Chapter Outliner (Study Companion)

Splits an ebook into chapters and sends each to Claude for analysis,
producing thesis, outline, key concepts, key passages, and critical
questions per chapter. Assembles results into an EPUB.

Usage:
    python3 outliner.py mybook.epub              # Run all stages
    python3 outliner.py mybook.epub --stage 3    # Re-run from stage 3
    python3 outliner.py mybook.epub --dry-run    # Stages 1-2 only
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

from shared import (
    BASE_DIR, setup_book_dir, find_source_file, detect_chapter_tag,
    split_chapters, filter_chapters, get_api_key, estimate_tokens,
    markdown_to_html, wrap_response_html, detect_book_metadata,
    resolve_epub_path,
)

INSTRUCTIONS_FILE = BASE_DIR / "outliner_instructions.txt"


def stage3(chapter_files, output_dir, dry_run=False):
    """Send each chapter to the Claude API."""
    print("\n" + "=" * 60)
    print("STAGE 3: Send Chapters to Claude API")
    print("=" * 60)

    if dry_run:
        print("  --dry-run active: skipping API calls.")
        return []

    api_key = get_api_key()

    if not INSTRUCTIONS_FILE.exists():
        print(f"ERROR: {INSTRUCTIONS_FILE} not found.")
        sys.exit(1)
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()

    chapters = []
    total_input_tokens = 0
    for fp in sorted(chapter_files):
        content = fp.read_text(encoding="utf-8")
        chapters.append((fp, content))
        total_input_tokens += estimate_tokens(content)

    instruction_tokens = estimate_tokens(instructions)
    total_input_tokens += instruction_tokens * len(chapters)

    estimated_input_cost = (total_input_tokens / 1_000_000) * 3.0
    estimated_output_cost = (total_input_tokens / 1_000_000) * 15.0
    estimated_total = estimated_input_cost + estimated_output_cost

    print(f"\nChapters to process: {len(chapters)}")
    print(f"Estimated total input tokens: ~{total_input_tokens:,}")
    print(f"Estimated cost: ~${estimated_total:.2f}")
    print()

    confirm = input("Proceed with API calls? [Y/n] ").strip()
    if confirm.lower() == "n":
        print("Aborted by user.")
        sys.exit(0)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    model = "claude-sonnet-4-5-20250929"

    results = []
    failures = []

    for i, (fp, content) in enumerate(chapters, start=1):
        print(f"Processing chapter {i}/{len(chapters)}: {fp.name}…", end=" ", flush=True)

        summary_name = fp.stem + "_summary.html"
        summary_path = output_dir / summary_name
        if summary_path.exists():
            print("already processed, skipping.")
            results.append((fp, summary_path.read_text(encoding="utf-8")))
            continue

        max_retries = 5
        backoff = 2
        response_text = None

        for attempt in range(max_retries):
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=instructions,
                    messages=[{"role": "user", "content": content}],
                )
                response_text = message.content[0].text
                break
            except anthropic.RateLimitError:
                wait = backoff * (2 ** attempt)
                print(f"rate limited, waiting {wait}s…", end=" ", flush=True)
                time.sleep(wait)
            except anthropic.APIError as e:
                wait = backoff * (2 ** attempt)
                print(f"API error ({e}), retrying in {wait}s…", end=" ", flush=True)
                time.sleep(wait)

        if response_text is not None:
            results.append((fp, response_text))
            print("done.")
        else:
            failures.append((fp, "Max retries exceeded"))
            print("FAILED.")

    if failures:
        print(f"\n{len(failures)} chapter(s) failed:")
        for fp, err in failures:
            print(f"  {fp.name}: {err}")
        print("You can re-run with --stage 3 to retry failed chapters.")

    return results


def stage4(results, chapter_files, output_dir):
    """Save API responses."""
    print("\n" + "=" * 60)
    print("STAGE 4: Save Processed Chapters")
    print("=" * 60)

    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    for chapter_path, response_text in results:
        summary_name = chapter_path.stem + "_summary.html"
        summary_path = output_dir / summary_name
        response_text = wrap_response_html(response_text, chapter_path, "summary")
        summary_path.write_text(response_text, encoding="utf-8")
        saved.append(summary_path)
        print(f"  Saved: {summary_name}")

    expected = {fp.stem + "_summary.html" for fp in chapter_files}
    actual = {p.name for p in output_dir.iterdir() if p.suffix == ".html"}
    missing = expected - actual

    if missing:
        print(f"\nWARNING: {len(missing)} summary file(s) missing:")
        for m in sorted(missing):
            print(f"  {m}")
    else:
        print(f"\nAll {len(expected)} chapter summaries present.")

    return saved


def stage5(paths):
    """Assemble final EPUB."""
    print("\n" + "=" * 60)
    print("STAGE 5: Assemble Final Ebook")
    print("=" * 60)

    from ebooklib import epub

    summary_dir = paths["chapter_summaries_dir"]
    summary_files = sorted(summary_dir.glob("*.html"))
    if not summary_files:
        print(f"ERROR: No summary files found in {summary_dir}/.")
        sys.exit(1)

    book_title, book_author = detect_book_metadata(paths["source_dir"])

    book = epub.EpubBook()
    book.set_identifier("ebook-processor-" + re.sub(r"\W+", "-", book_title.lower()))
    book.set_title(book_title)
    book.set_language("en")
    book.add_author(book_author)

    css_content = """
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.6; margin: 1em; color: #222; }
h1, h2, h3, h4 { margin-top: 1.5em; margin-bottom: 0.5em; line-height: 1.2; }
p { margin-bottom: 0.8em; text-align: justify; }
blockquote { margin: 1em 2em; font-style: italic; color: #555; }
"""
    css = epub.EpubItem(uid="style", file_name="style/default.css",
                        media_type="text/css", content=css_content.encode("utf-8"))
    book.add_item(css)

    epub_chapters = []
    toc = []

    for i, sf in enumerate(summary_files, start=1):
        content = sf.read_text(encoding="utf-8")
        chap_soup = BeautifulSoup(content, "lxml")
        title_el = chap_soup.find(["h1", "h2", "h3", "h4"])
        chap_title = title_el.get_text(strip=True) if title_el else sf.stem
        body = chap_soup.find("body")
        body_html = "".join(str(c) for c in body.children) if body else content

        chap = epub.EpubHtml(title=chap_title, file_name=f"chapter_{i:02d}.xhtml", lang="en")
        chap.content = (
            f'<html><head><link rel="stylesheet" href="style/default.css" '
            f'type="text/css"/></head><body>{body_html}</body></html>'
        ).encode("utf-8")
        chap.add_item(css)
        book.add_item(chap)
        epub_chapters.append(chap)
        toc.append(epub.Link(f"chapter_{i:02d}.xhtml", chap_title, f"ch{i}"))

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    output_dir = paths["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"\s+", "_", re.sub(r"[^\w\s\-]", "", book_title).strip())

    epub_path = output_dir / f"{safe_title}_summaries.epub"
    epub.write_epub(str(epub_path), book)
    print(f"\nEPUB saved: {epub_path}")

    # HTML backup
    html_parts = [
        f"<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{book_title} — Summaries</title>\n<style>{css_content}</style>\n"
        f"</head>\n<body>\n<h1>{book_title}</h1>\n"
    ]
    for sf in summary_files:
        content = sf.read_text(encoding="utf-8")
        chap_soup = BeautifulSoup(content, "lxml")
        body = chap_soup.find("body")
        if body:
            html_parts.append("".join(str(c) for c in body.children))
        else:
            html_parts.append(content)
        html_parts.append("\n<hr/>\n")
    html_parts.append("</body>\n</html>")

    html_path = output_dir / f"{safe_title}_summaries.html"
    html_path.write_text("".join(html_parts), encoding="utf-8")
    print(f"HTML saved: {html_path}")

    return epub_path


def main():
    parser = argparse.ArgumentParser(description="Ebook Chapter Processing Pipeline")
    parser.add_argument("book", help="Path to EPUB file or book slug")
    parser.add_argument("--stage", type=int, choices=[1, 2, 3, 4, 5], default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    epub_path = resolve_epub_path(args.book)
    paths = setup_book_dir(epub_path)

    print("=" * 60)
    print("  Ebook Chapter Processing Pipeline")
    print("=" * 60)
    print(f"  Book: {epub_path.stem}")
    print(f"  Working dir: {paths['book_dir'].relative_to(BASE_DIR)}")
    if args.dry_run:
        print("  Mode: DRY RUN")
    print(f"  Starting from stage: {args.stage}")

    html_path = find_source_file(paths)
    chapter_tag = None
    chapter_files = None
    results = None

    if args.stage <= 1:
        chapter_tag = detect_chapter_tag(html_path)

    if args.stage <= 2:
        if chapter_tag is None:
            chapter_tag = detect_chapter_tag(html_path)
        chapter_files = split_chapters(html_path, chapter_tag, paths["chapters_dir"])
    else:
        chapter_files = sorted(paths["chapters_dir"].glob("*.html"))
        if not chapter_files:
            print("ERROR: No chapter files found. Run from stage 1 first.")
            sys.exit(1)

    chapter_files = filter_chapters(chapter_files, paths["selection_file"], "processing")

    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN COMPLETE")
        print("=" * 60)
        return

    if args.stage <= 3:
        results = stage3(chapter_files, paths["chapter_summaries_dir"])

    if args.stage <= 4:
        if results is None:
            results = []
        if results:
            stage4(results, chapter_files, paths["chapter_summaries_dir"])

    if args.stage <= 5:
        stage5(paths)

    print("\n" + "=" * 60)
    print("  Pipeline complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
