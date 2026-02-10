#!/usr/bin/env python3
"""
Ebook Chapter Processing Pipeline

Splits an ebook HTML file into chapters, sends each chapter to the Claude API
for processing, then reassembles the results into a Kindle-compliant EPUB.

Usage:
    python pipeline.py                  # Run all stages
    python pipeline.py --stage 3        # Re-run from stage 3
    python pipeline.py --dry-run        # Stages 1-2 only (no API calls)
"""

import argparse
import os
import re
import sys
import time
import glob
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = BASE_DIR / "source"
CHAPTERS_DIR = BASE_DIR / "chapters"
SUMMARIES_DIR = BASE_DIR / "chapter_summaries"
OUTPUT_DIR = BASE_DIR / "output"
INSTRUCTIONS_FILE = BASE_DIR / "instructions.txt"

HEADING_TAGS = ["h1", "h2", "h3", "h4"]


# ===========================================================================
# Stage 1 – Identify the chapter heading tag
# ===========================================================================
def stage1(html_path: Path) -> str:
    """Detect which heading tag marks chapter titles."""
    print("\n" + "=" * 60)
    print("STAGE 1: Identify Chapter Heading Tag")
    print("=" * 60)

    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    # Gather counts and texts for each heading level
    heading_info: dict[str, list[str]] = {}
    for tag in HEADING_TAGS:
        elements = soup.find_all(tag)
        texts = [el.get_text(strip=True) for el in elements]
        if texts:
            heading_info[tag] = texts

    if not heading_info:
        print("ERROR: No heading tags (h1-h4) found in the source file.")
        sys.exit(1)

    # Print what we found
    print("\nHeading tag survey:")
    for tag, texts in heading_info.items():
        print(f"  <{tag}> — {len(texts)} occurrence(s)")
        for t in texts[:5]:
            print(f"         • {t[:80]}")
        if len(texts) > 5:
            print(f"         … and {len(texts) - 5} more")

    # Heuristic scoring
    scores: dict[str, float] = {}
    chapter_pattern = re.compile(
        r"(chapter|part|section|book|act|prologue|epilogue)\s*\d*",
        re.IGNORECASE,
    )

    for tag, texts in heading_info.items():
        count = len(texts)
        if count < 2:
            # A single heading is unlikely to be the chapter marker
            scores[tag] = 0
            continue

        score = count  # base score: more headings → more likely

        # Bonus for chapter-like text
        chapter_matches = sum(1 for t in texts if chapter_pattern.search(t))
        score += chapter_matches * 2

        # Bonus for numbered patterns (e.g. "1", "2", …)
        numbered = sum(1 for t in texts if re.search(r"\d+", t))
        score += numbered * 0.5

        # Prefer h2/h3 over h1 (h1 is often just the book title)
        if tag == "h1":
            score *= 0.6
        elif tag in ("h2", "h3"):
            score *= 1.2

        scores[tag] = score

    if not scores or max(scores.values()) == 0:
        print("ERROR: Could not determine chapter heading tag.")
        sys.exit(1)

    best_tag = max(scores, key=lambda t: scores[t])
    runner_up = sorted(scores, key=lambda t: scores[t], reverse=True)

    # If the top two scores are close, ask the user
    ambiguous = False
    if len(runner_up) >= 2:
        s1, s2 = scores[runner_up[0]], scores[runner_up[1]]
        if s2 > 0 and s1 / max(s2, 0.01) < 1.5:
            ambiguous = True

    print(f"\nDetected chapter heading tag: <{best_tag}>")

    if ambiguous:
        print(
            f"  (Runner-up: <{runner_up[1]}> — scores are close, "
            "please confirm)"
        )
        answer = input(
            f"Use <{best_tag}> as the chapter heading? [Y/n] "
        ).strip()
        if answer.lower() == "n":
            alt = input("Enter the tag to use instead (e.g. h2): ").strip().lower()
            if alt in heading_info:
                best_tag = alt
                print(f"Using <{best_tag}> as chapter heading tag.")
            else:
                print(f"Tag <{alt}> not found in the document. Aborting.")
                sys.exit(1)

    return best_tag


# ===========================================================================
# Stage 2 – Split into chapter files
# ===========================================================================
def _sanitize_filename(text: str, max_len: int = 60) -> str:
    """Sanitize text for use as a filename component."""
    text = text.strip()
    # Remove HTML entities leftovers
    text = re.sub(r"&\w+;", "", text)
    # Keep only safe characters
    text = re.sub(r"[^\w\s\-]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def stage2(html_path: Path, chapter_tag: str) -> list[Path]:
    """Split the source HTML into individual chapter files."""
    print("\n" + "=" * 60)
    print("STAGE 2: Split into Chapter Files")
    print("=" * 60)

    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    headings = soup.find_all(chapter_tag)
    if not headings:
        print(f"ERROR: No <{chapter_tag}> tags found.")
        sys.exit(1)

    # Build list of (heading_element, title_text)
    chapter_data: list[tuple[str, str]] = []
    for i, heading in enumerate(headings):
        title = heading.get_text(strip=True)

        # Collect all content from this heading until the next one
        parts = [str(heading)]
        for sibling in heading.find_next_siblings():
            if sibling.name == chapter_tag:
                break
            parts.append(str(sibling))

        chapter_html = "\n".join(parts)
        chapter_data.append((title, chapter_html))

    # Write files
    CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for idx, (title, html_content) in enumerate(chapter_data, start=1):
        safe_title = _sanitize_filename(title) or f"Chapter_{idx}"
        filename = f"{idx:02d} - {safe_title}.html"
        filepath = CHAPTERS_DIR / filename

        full_html = (
            "<!DOCTYPE html>\n<html>\n<head>\n"
            '<meta charset="utf-8">\n'
            f"<title>{title}</title>\n"
            "</head>\n<body>\n"
            f"{html_content}\n"
            "</body>\n</html>"
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_html)
        written.append(filepath)

    print(f"\nSplit into {len(written)} chapters:")
    for p in written:
        print(f"  {p.name}")

    return written


# ===========================================================================
# Stage 3 – Send each chapter to the Claude API
# ===========================================================================
def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


def stage3(chapter_files: list[Path], dry_run: bool = False) -> list[tuple[Path, str]]:
    """Send each chapter to the Claude API and return (path, response) pairs."""
    print("\n" + "=" * 60)
    print("STAGE 3: Send Chapters to Claude API")
    print("=" * 60)

    if dry_run:
        print("  --dry-run active: skipping API calls.")
        return []

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Export it before running: export ANTHROPIC_API_KEY='sk-...'"
        )
        sys.exit(1)

    # Read instructions
    if not INSTRUCTIONS_FILE.exists():
        print(f"ERROR: {INSTRUCTIONS_FILE} not found.")
        sys.exit(1)
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()

    # Read all chapters and estimate cost
    chapters: list[tuple[Path, str]] = []
    total_input_tokens = 0
    for fp in sorted(chapter_files):
        content = fp.read_text(encoding="utf-8")
        chapters.append((fp, content))
        total_input_tokens += _estimate_tokens(content)

    # Add instruction tokens for each call
    instruction_tokens = _estimate_tokens(instructions)
    total_input_tokens += instruction_tokens * len(chapters)

    # Rough cost estimate (claude-sonnet-4-5-20250929 pricing: $3/M input, $15/M output)
    # Assume output ≈ input size
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

    results: list[tuple[Path, str]] = []
    failures: list[tuple[Path, str]] = []

    for i, (fp, content) in enumerate(chapters, start=1):
        print(f"Processing chapter {i}/{len(chapters)}: {fp.name}…", end=" ", flush=True)

        # Check if already processed (resume support)
        summary_name = fp.stem + "_summary.html"
        summary_path = SUMMARIES_DIR / summary_name
        if summary_path.exists():
            print("already processed, skipping.")
            results.append((fp, summary_path.read_text(encoding="utf-8")))
            continue

        # Retry with exponential backoff
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
        print(f"\n⚠ {len(failures)} chapter(s) failed:")
        for fp, err in failures:
            print(f"  {fp.name}: {err}")
        print("You can re-run with --stage 3 to retry failed chapters.")

    return results


# ===========================================================================
# Stage 4 – Save processed chapters
# ===========================================================================
def stage4(
    results: list[tuple[Path, str]], chapter_files: list[Path]
) -> list[Path]:
    """Save API responses to chapter_summaries/ and validate completeness."""
    print("\n" + "=" * 60)
    print("STAGE 4: Save Processed Chapters")
    print("=" * 60)

    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for chapter_path, response_text in results:
        summary_name = chapter_path.stem + "_summary.html"
        summary_path = SUMMARIES_DIR / summary_name

        # If response is not HTML, wrap it
        if not re.search(r"<\w+[\s>]", response_text):
            # Extract chapter title from original file
            original = chapter_path.read_text(encoding="utf-8")
            title_match = re.search(r"<title>(.*?)</title>", original)
            title = title_match.group(1) if title_match else chapter_path.stem
            response_text = (
                "<!DOCTYPE html>\n<html>\n<head>\n"
                '<meta charset="utf-8">\n'
                f"<title>{title}</title>\n"
                "</head>\n<body>\n"
                f"<h2>{title}</h2>\n"
                f"<p>{response_text}</p>\n"
                "</body>\n</html>"
            )

        summary_path.write_text(response_text, encoding="utf-8")
        saved.append(summary_path)
        print(f"  Saved: {summary_name}")

    # Validate completeness
    expected = {fp.stem + "_summary.html" for fp in chapter_files}
    actual = {p.name for p in SUMMARIES_DIR.iterdir() if p.suffix == ".html"}
    missing = expected - actual

    if missing:
        print(f"\nWARNING: {len(missing)} chapter summary file(s) missing:")
        for m in sorted(missing):
            print(f"  {m}")
        print("Re-run with --stage 3 to process missing chapters.")
    else:
        print(f"\nAll {len(expected)} chapter summaries present.")

    return saved


# ===========================================================================
# Stage 5 – Assemble final EPUB
# ===========================================================================
def stage5(chapter_files: list[Path]) -> Path:
    """Assemble processed chapters into an EPUB file."""
    print("\n" + "=" * 60)
    print("STAGE 5: Assemble Final Ebook")
    print("=" * 60)

    from ebooklib import epub

    # Collect summary files in order
    summary_files = sorted(SUMMARIES_DIR.glob("*.html"))
    if not summary_files:
        print("ERROR: No summary files found in chapter_summaries/.")
        sys.exit(1)

    # Try to detect title/author from the source or ask the user
    source_files = list(SOURCE_DIR.glob("*.html")) + list(SOURCE_DIR.glob("*.htm"))
    book_title = ""
    book_author = ""

    if source_files:
        with open(source_files[0], "r", encoding="utf-8") as f:
            src_soup = BeautifulSoup(f, "lxml")
        title_tag = src_soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            book_title = title_tag.get_text(strip=True)
        # Try to find author in meta tags
        meta_author = src_soup.find("meta", attrs={"name": "author"})
        if meta_author:
            book_author = meta_author.get("content", "")

    if not book_title:
        book_title = input("Enter book title: ").strip() or "Untitled"
    else:
        confirm = input(f'Detected title: "{book_title}". Use this? [Y/n] ').strip()
        if confirm.lower() == "n":
            book_title = input("Enter book title: ").strip() or "Untitled"

    if not book_author:
        book_author = input("Enter author name: ").strip() or "Unknown"
    else:
        confirm = input(f'Detected author: "{book_author}". Use this? [Y/n] ').strip()
        if confirm.lower() == "n":
            book_author = input("Enter author name: ").strip() or "Unknown"

    # Create EPUB
    book = epub.EpubBook()
    book.set_identifier("ebook-processor-" + re.sub(r"\W+", "-", book_title.lower()))
    book.set_title(book_title)
    book.set_language("en")
    book.add_author(book_author)

    # CSS stylesheet
    css_content = """
body {
    font-family: Georgia, "Times New Roman", serif;
    line-height: 1.6;
    margin: 1em;
    color: #222;
}
h1, h2, h3, h4 {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    line-height: 1.2;
}
p {
    margin-bottom: 0.8em;
    text-align: justify;
}
blockquote {
    margin: 1em 2em;
    font-style: italic;
    color: #555;
}
"""
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=css_content.encode("utf-8"),
    )
    book.add_item(css)

    # Add chapters
    epub_chapters = []
    toc = []

    for i, sf in enumerate(summary_files, start=1):
        content = sf.read_text(encoding="utf-8")

        # Extract title from the HTML
        chap_soup = BeautifulSoup(content, "lxml")
        title_el = chap_soup.find(["h1", "h2", "h3", "h4"])
        chap_title = title_el.get_text(strip=True) if title_el else sf.stem

        # Extract just the body content
        body = chap_soup.find("body")
        body_html = "".join(str(c) for c in body.children) if body else content

        chap = epub.EpubHtml(
            title=chap_title,
            file_name=f"chapter_{i:02d}.xhtml",
            lang="en",
        )
        chap.content = (
            f'<html><head><link rel="stylesheet" href="style/default.css" '
            f'type="text/css"/></head><body>{body_html}</body></html>'
        ).encode("utf-8")
        chap.add_item(css)

        book.add_item(chap)
        epub_chapters.append(chap)
        toc.append(epub.Link(f"chapter_{i:02d}.xhtml", chap_title, f"ch{i}"))

    # Table of contents
    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Spine
    book.spine = ["nav"] + epub_chapters

    # Write EPUB
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^\w\s\-]", "", book_title)
    safe_title = re.sub(r"\s+", "_", safe_title.strip())

    epub_path = OUTPUT_DIR / f"{safe_title}_summaries.epub"
    epub.write_epub(str(epub_path), book)
    print(f"\nEPUB saved: {epub_path}")

    # Also generate single HTML backup
    html_parts = [
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{book_title} — Summaries</title>\n"
        f"<style>{css_content}</style>\n"
        "</head>\n<body>\n"
        f"<h1>{book_title}</h1>\n"
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

    html_path = OUTPUT_DIR / f"{safe_title}_summaries.html"
    html_path.write_text("".join(html_parts), encoding="utf-8")
    print(f"HTML saved: {html_path}")

    return epub_path


# ===========================================================================
# Master orchestration
# ===========================================================================
def find_source_file() -> Path:
    """Locate the single HTML source file."""
    patterns = list(SOURCE_DIR.glob("*.html")) + list(SOURCE_DIR.glob("*.htm"))
    if not patterns:
        print(f"ERROR: No HTML files found in {SOURCE_DIR}/")
        print("Place your source ebook .html file in the source/ directory.")
        sys.exit(1)
    if len(patterns) > 1:
        print(f"WARNING: Multiple HTML files found in {SOURCE_DIR}/:")
        for p in patterns:
            print(f"  {p.name}")
        print("Using the first one.")
    return patterns[0]


def get_chapter_files() -> list[Path]:
    """Get sorted list of chapter files from the chapters directory."""
    files = sorted(CHAPTERS_DIR.glob("*.html"))
    if not files:
        print(f"ERROR: No chapter files found in {CHAPTERS_DIR}/")
        print("Run stages 1-2 first.")
        sys.exit(1)
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Ebook Chapter Processing Pipeline"
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=[1, 2, 3, 4, 5],
        default=1,
        help="Stage to start from (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run stages 1-2 only (no API calls)",
    )
    args = parser.parse_args()

    start_stage = args.stage
    dry_run = args.dry_run

    print("=" * 60)
    print("  Ebook Chapter Processing Pipeline")
    print("=" * 60)
    if dry_run:
        print("  Mode: DRY RUN (stages 1-2 only)")
    print(f"  Starting from stage: {start_stage}")

    html_path = find_source_file()
    print(f"  Source file: {html_path.name}")

    chapter_tag = None
    chapter_files = None
    results = None

    # Stage 1
    if start_stage <= 1:
        chapter_tag = stage1(html_path)
    else:
        print(f"\nSkipping Stage 1 (starting from stage {start_stage})")

    # Stage 2
    if start_stage <= 2:
        if chapter_tag is None:
            # Need to re-detect if skipping stage 1 but running stage 2
            chapter_tag = stage1(html_path)
        chapter_files = stage2(html_path, chapter_tag)
    else:
        print(f"Skipping Stage 2 (starting from stage {start_stage})")
        chapter_files = get_chapter_files()

    if dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN COMPLETE — Stages 1-2 finished.")
        print(f"Review chapter files in: {CHAPTERS_DIR}/")
        print("Re-run without --dry-run to proceed with API calls.")
        print("=" * 60)
        return

    # Stage 3
    if start_stage <= 3:
        results = stage3(chapter_files)
    else:
        print(f"Skipping Stage 3 (starting from stage {start_stage})")

    # Stage 4
    if start_stage <= 4:
        if results is None:
            # If starting from stage 4, we need to load existing summaries
            # that were already saved (results aren't needed for validation)
            results = []
        if results:
            stage4(results, chapter_files)
        else:
            print("\nStage 4: No new results to save. Validating existing summaries…")
            expected = {fp.stem + "_summary.html" for fp in chapter_files}
            actual = {
                p.name
                for p in SUMMARIES_DIR.iterdir()
                if p.suffix == ".html"
            } if SUMMARIES_DIR.exists() else set()
            missing = expected - actual
            if missing:
                print(f"  WARNING: {len(missing)} summary file(s) missing:")
                for m in sorted(missing):
                    print(f"    {m}")
            else:
                print(f"  All {len(expected)} chapter summaries present.")

    # Stage 5
    if start_stage <= 5:
        stage5(chapter_files)

    print("\n" + "=" * 60)
    print("  Pipeline complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
