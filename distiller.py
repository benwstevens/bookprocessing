#!/usr/bin/env python3
"""
Ebook Distiller Pipeline — Hierarchical Summarization

Distills a full-length book (~100K+ words) down to ~10,000 words via a
two-pass strategy:

  Pass 1: Summarize each chapter individually with a proportional word target
  Pass 2: Coherence pass — merge all chapter summaries and refine into a
           unified ~10,000 word document

Usage:
    python distiller.py                  # Run all stages
    python distiller.py --stage 3        # Re-run from stage 3
    python distiller.py --dry-run        # Stages 1-2 only (no API calls)
    python distiller.py --target 10000   # Set total word target (default: 10000)
"""

import argparse
import os
import re
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = BASE_DIR / "source"
CHAPTERS_DIR = BASE_DIR / "chapters"
DISTILLED_DIR = BASE_DIR / "distilled_chapters"
OUTPUT_DIR = BASE_DIR / "output"
INSTRUCTIONS_FILE = BASE_DIR / "distiller_instructions.txt"
COHERENCE_INSTRUCTIONS_FILE = BASE_DIR / "distiller_coherence_instructions.txt"

HEADING_TAGS = ["h1", "h2", "h3", "h4"]


# ===========================================================================
# Utility: word count
# ===========================================================================
def _count_words(text: str) -> int:
    """Count words in text, stripping HTML tags first."""
    clean = re.sub(r"<[^>]+>", " ", text)
    return len(clean.split())


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


# ===========================================================================
# Utility: get API key
# ===========================================================================
def _get_api_key() -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip("'\"")
                    break
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY not found.\n"
            "Set it in the .env file in the project root:\n"
            "  1. Open the file called .env\n"
            "  2. Replace YOUR_KEY_HERE with your actual API key\n"
            "Get a key at: https://console.anthropic.com/settings/keys"
        )
        sys.exit(1)
    return api_key


# ===========================================================================
# Utility: Markdown-to-HTML fallback
# ===========================================================================
def _markdown_to_html(text: str) -> str:
    """Convert common Markdown patterns to HTML if Markdown is detected."""
    if not re.search(r"(^#{1,4}\s|\*\*|^>\s|^- )", text, re.MULTILINE):
        return text

    lines = text.split("\n")
    html_lines = []
    in_list = False
    in_blockquote = False

    for line in lines:
        stripped = line.strip()

        heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading_match:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_blockquote:
                html_lines.append("</blockquote>")
                in_blockquote = False
            level = len(heading_match.group(1))
            h_level = min(level + 1, 4)
            html_lines.append(f"<h{h_level}>{heading_match.group(2).strip()}</h{h_level}>")
            continue

        if stripped.startswith("> "):
            if not in_blockquote:
                html_lines.append("<blockquote>")
                in_blockquote = True
            html_lines.append(f"<p>{stripped[2:]}</p>")
            continue
        elif in_blockquote and stripped:
            html_lines.append("</blockquote>")
            in_blockquote = False

        if re.match(r"^[-*]\s+", stripped):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            item_text = re.sub(r"^[-*]\s+", "", stripped)
            html_lines.append(f"<li>{item_text}</li>")
            continue
        elif in_list and not stripped:
            html_lines.append("</ul>")
            in_list = False

        if not stripped:
            continue

        html_lines.append(f"<p>{stripped}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_blockquote:
        html_lines.append("</blockquote>")

    result = "\n".join(html_lines)
    result = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"\*(.+?)\*", r"<em>\1</em>", result)

    return result


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

    heading_info: dict[str, list[str]] = {}
    for tag in HEADING_TAGS:
        elements = soup.find_all(tag)
        texts = [el.get_text(strip=True) for el in elements]
        if texts:
            heading_info[tag] = texts

    if not heading_info:
        all_tags = Counter(tag.name for tag in soup.find_all(True))
        print("ERROR: No heading tags (h1-h4) found in the source file.")
        print("\nMost common tags found:")
        for tag_name, count in all_tags.most_common(10):
            print(f"  <{tag_name}> — {count}")
        print("\nIf converting from EPUB, try deleting the .html file in")
        print("source/ and re-running so it reconverts from the EPUB.")
        sys.exit(1)

    print("\nHeading tag survey:")
    for tag, texts in heading_info.items():
        print(f"  <{tag}> — {len(texts)} occurrence(s)")
        for t in texts[:5]:
            print(f"         . {t[:80]}")
        if len(texts) > 5:
            print(f"         ... and {len(texts) - 5} more")

    scores: dict[str, float] = {}
    chapter_pattern = re.compile(
        r"(chapter|part|section|book|act|prologue|epilogue)\s*\d*",
        re.IGNORECASE,
    )

    for tag, texts in heading_info.items():
        count = len(texts)
        if count < 2:
            scores[tag] = 0
            continue
        score = count
        chapter_matches = sum(1 for t in texts if chapter_pattern.search(t))
        score += chapter_matches * 2
        numbered = sum(1 for t in texts if re.search(r"\d+", t))
        score += numbered * 0.5
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

    ambiguous = False
    if len(runner_up) >= 2:
        s1, s2 = scores[runner_up[0]], scores[runner_up[1]]
        if s2 > 0 and s1 / max(s2, 0.01) < 1.5:
            ambiguous = True

    print(f"\nDetected chapter heading tag: <{best_tag}>")

    if ambiguous:
        print(f"  (Runner-up: <{runner_up[1]}> — scores are close, please confirm)")
        answer = input(f"Use <{best_tag}> as the chapter heading? [Y/n] ").strip()
        if answer.lower() == "n":
            alt = input("Enter the tag to use instead (e.g. h2): ").strip().lower()
            if alt in heading_info:
                best_tag = alt
            else:
                print(f"Tag <{alt}> not found in the document. Aborting.")
                sys.exit(1)

    return best_tag


# ===========================================================================
# Stage 2 – Split into chapter files
# ===========================================================================
def _sanitize_filename(text: str, max_len: int = 60) -> str:
    text = text.strip()
    text = re.sub(r"&\w+;", "", text)
    text = re.sub(r"[^\w\s\-]", "", text)
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

    chapter_data: list[tuple[str, str]] = []
    for i, heading in enumerate(headings):
        title = heading.get_text(strip=True)
        parts = [str(heading)]
        for sibling in heading.find_next_siblings():
            if sibling.name == chapter_tag:
                break
            parts.append(str(sibling))
        chapter_html = "\n".join(parts)
        chapter_data.append((title, chapter_html))

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
# Stage 3 – Pass 1: Summarize each chapter with proportional word targets
# ===========================================================================
def stage3(
    chapter_files: list[Path], target_words: int, dry_run: bool = False
) -> list[tuple[Path, str]]:
    """Summarize each chapter proportionally to hit the total word target."""
    print("\n" + "=" * 60)
    print("STAGE 3: Pass 1 — Distill Each Chapter")
    print("=" * 60)

    if dry_run:
        print("  --dry-run active: skipping API calls.")
        return []

    api_key = _get_api_key()

    if not INSTRUCTIONS_FILE.exists():
        print(f"ERROR: {INSTRUCTIONS_FILE} not found.")
        sys.exit(1)
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()

    # Read all chapters and compute word counts
    chapters: list[tuple[Path, str, int]] = []
    total_words = 0
    for fp in sorted(chapter_files):
        content = fp.read_text(encoding="utf-8")
        wc = _count_words(content)
        chapters.append((fp, content, wc))
        total_words += wc

    # Calculate proportional word targets per chapter
    print(f"\nTotal source words: {total_words:,}")
    print(f"Target output: ~{target_words:,} words")
    print(f"Compression ratio: ~{total_words / target_words:.1f}:1")
    print(f"\nPer-chapter targets (proportional to length):")

    chapter_targets: list[tuple[Path, str, int]] = []
    for fp, content, wc in chapters:
        proportion = wc / total_words
        chapter_target = max(int(target_words * proportion), 150)  # minimum 150 words
        chapter_targets.append((fp, content, chapter_target))
        print(f"  {fp.name}: {wc:,} words -> ~{chapter_target:,} words")

    # Cost estimate
    total_input_tokens = sum(_estimate_tokens(c) for _, c, _ in chapter_targets)
    instruction_tokens = _estimate_tokens(instructions)
    total_input_tokens += instruction_tokens * len(chapter_targets)
    estimated_output_tokens = _estimate_tokens(" " * target_words * 5)  # rough
    estimated_input_cost = (total_input_tokens / 1_000_000) * 3.0
    estimated_output_cost = (estimated_output_tokens / 1_000_000) * 15.0
    # Add coherence pass estimate
    coherence_input = _estimate_tokens(" " * target_words * 6)
    coherence_output = _estimate_tokens(" " * target_words * 5)
    estimated_total = (
        estimated_input_cost
        + estimated_output_cost
        + (coherence_input / 1_000_000) * 3.0
        + (coherence_output / 1_000_000) * 15.0
    )

    print(f"\nEstimated total cost (both passes): ~${estimated_total:.2f}")
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

    for i, (fp, content, word_target) in enumerate(chapter_targets, start=1):
        print(
            f"Distilling chapter {i}/{len(chapter_targets)}: {fp.name} "
            f"(target: ~{word_target} words)…",
            end=" ",
            flush=True,
        )

        # Check if already processed (resume support)
        distilled_name = fp.stem + "_distilled.html"
        distilled_path = DISTILLED_DIR / distilled_name
        if distilled_path.exists():
            print("already processed, skipping.")
            results.append((fp, distilled_path.read_text(encoding="utf-8")))
            continue

        # Build the user message with the word target
        user_message = (
            f"TARGET WORD COUNT: {word_target} words (stay within 10% of this).\n"
            f"CHAPTER TITLE: {fp.stem}\n\n"
            f"{content}"
        )

        max_retries = 5
        backoff = 2
        response_text = None

        for attempt in range(max_retries):
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=instructions,
                    messages=[{"role": "user", "content": user_message}],
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
            actual_words = _count_words(response_text)
            results.append((fp, response_text))
            print(f"done ({actual_words} words).")
        else:
            failures.append((fp, "Max retries exceeded"))
            print("FAILED.")

    if failures:
        print(f"\n{len(failures)} chapter(s) failed:")
        for fp, err in failures:
            print(f"  {fp.name}: {err}")
        print("You can re-run with --stage 3 to retry failed chapters.")

    return results


# ===========================================================================
# Stage 4 – Save distilled chapters
# ===========================================================================
def stage4(
    results: list[tuple[Path, str]], chapter_files: list[Path]
) -> list[Path]:
    """Save Pass 1 results to distilled_chapters/."""
    print("\n" + "=" * 60)
    print("STAGE 4: Save Distilled Chapters")
    print("=" * 60)

    DISTILLED_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    total_words = 0

    for chapter_path, response_text in results:
        distilled_name = chapter_path.stem + "_distilled.html"
        distilled_path = DISTILLED_DIR / distilled_name

        response_text = _markdown_to_html(response_text)

        if not re.search(r"<\w+[\s>]", response_text):
            original = chapter_path.read_text(encoding="utf-8")
            title_match = re.search(r"<title>(.*?)</title>", original)
            title = title_match.group(1) if title_match else chapter_path.stem
            response_text = f"<h2>{title}</h2>\n<p>{response_text}</p>\n"

        original = chapter_path.read_text(encoding="utf-8")
        title_match = re.search(r"<title>(.*?)</title>", original)
        title = title_match.group(1) if title_match else chapter_path.stem
        response_text = (
            "<!DOCTYPE html>\n<html>\n<head>\n"
            '<meta charset="utf-8">\n'
            f"<title>{title}</title>\n"
            "</head>\n<body>\n"
            f"{response_text}\n"
            "</body>\n</html>"
        )

        distilled_path.write_text(response_text, encoding="utf-8")
        saved.append(distilled_path)
        wc = _count_words(response_text)
        total_words += wc
        print(f"  Saved: {distilled_name} ({wc} words)")

    print(f"\nTotal distilled words: {total_words:,}")

    # Validate completeness
    expected = {fp.stem + "_distilled.html" for fp in chapter_files}
    actual = {p.name for p in DISTILLED_DIR.iterdir() if p.suffix == ".html"}
    missing = expected - actual

    if missing:
        print(f"\nWARNING: {len(missing)} distilled chapter(s) missing:")
        for m in sorted(missing):
            print(f"  {m}")
        print("Re-run with --stage 3 to process missing chapters.")
    else:
        print(f"All {len(expected)} distilled chapters present.")

    return saved


# ===========================================================================
# Stage 5 – Pass 2: Coherence pass + assemble final EPUB
# ===========================================================================
def stage5(chapter_files: list[Path], target_words: int) -> Path:
    """Merge all distilled chapters, run coherence pass, assemble EPUB."""
    print("\n" + "=" * 60)
    print("STAGE 5: Pass 2 — Coherence Edit + Final Assembly")
    print("=" * 60)

    from ebooklib import epub

    distilled_files = sorted(DISTILLED_DIR.glob("*.html"))
    if not distilled_files:
        print("ERROR: No distilled files found in distilled_chapters/.")
        sys.exit(1)

    # Detect title/author
    source_files = list(SOURCE_DIR.glob("*.html")) + list(SOURCE_DIR.glob("*.htm"))
    book_title = ""
    book_author = ""

    if source_files:
        with open(source_files[0], "r", encoding="utf-8") as f:
            src_soup = BeautifulSoup(f, "lxml")
        title_tag = src_soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            book_title = title_tag.get_text(strip=True)
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

    # Concatenate all distilled chapters
    combined_parts = []
    total_pre_coherence = 0
    for sf in distilled_files:
        content = sf.read_text(encoding="utf-8")
        chap_soup = BeautifulSoup(content, "lxml")
        body = chap_soup.find("body")
        if body:
            body_html = "".join(str(c) for c in body.children)
        else:
            body_html = content
        combined_parts.append(body_html)
        combined_parts.append("\n<hr/>\n")
        total_pre_coherence += _count_words(body_html)

    combined_html = "\n".join(combined_parts)

    print(f"\nTotal words before coherence pass: {total_pre_coherence:,}")
    print(f"Target: ~{target_words:,} words")

    # Check if coherence pass output already exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^\w\s\-]", "", book_title)
    safe_title = re.sub(r"\s+", "_", safe_title.strip())
    coherence_cache = OUTPUT_DIR / f"{safe_title}_coherence_cache.html"

    if coherence_cache.exists():
        print("Found cached coherence pass output. Using it.")
        final_html = coherence_cache.read_text(encoding="utf-8")
    else:
        # Run the coherence pass
        print("Running coherence pass (this may take a minute)…")

        if not COHERENCE_INSTRUCTIONS_FILE.exists():
            print(f"WARNING: {COHERENCE_INSTRUCTIONS_FILE} not found.")
            print("Skipping coherence pass — using raw chapter summaries.")
            final_html = combined_html
        else:
            api_key = _get_api_key()
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            coherence_instructions = COHERENCE_INSTRUCTIONS_FILE.read_text(
                encoding="utf-8"
            ).strip()

            user_message = (
                f"BOOK TITLE: {book_title}\n"
                f"AUTHOR: {book_author}\n"
                f"TOTAL CHAPTERS: {len(distilled_files)} — every one must appear in your output.\n\n"
                f"Here are the chapter-by-chapter summaries:\n\n"
                f"{combined_html}"
            )

            max_retries = 5
            backoff = 2
            final_html = None

            for attempt in range(max_retries):
                try:
                    # Use streaming for large outputs to avoid timeout
                    collected_text = []
                    with client.messages.stream(
                        model="claude-sonnet-4-5-20250929",
                        max_tokens=32768,
                        system=coherence_instructions,
                        messages=[{"role": "user", "content": user_message}],
                    ) as stream:
                        for text in stream.text_stream:
                            collected_text.append(text)
                            # Print a dot every ~500 chars to show progress
                            if sum(len(t) for t in collected_text) % 2000 < len(text):
                                print(".", end="", flush=True)
                    final_html = "".join(collected_text)
                    print()  # newline after dots
                    break
                except anthropic.RateLimitError:
                    wait = backoff * (2 ** attempt)
                    print(f"  Rate limited, waiting {wait}s…")
                    time.sleep(wait)
                except anthropic.APIError as e:
                    wait = backoff * (2 ** attempt)
                    print(f"  API error ({e}), retrying in {wait}s…")
                    time.sleep(wait)

            if final_html is None:
                print("  Coherence pass failed. Using raw chapter summaries.")
                final_html = combined_html
            else:
                final_html = _markdown_to_html(final_html)
                final_words = _count_words(final_html)
                print(f"  Coherence pass complete: {final_words:,} words")
                # Cache it
                coherence_cache.write_text(final_html, encoding="utf-8")

    # Build EPUB
    book = epub.EpubBook()
    book.set_identifier("ebook-distilled-" + re.sub(r"\W+", "-", book_title.lower()))
    book.set_title(book_title + " (Distilled)")
    book.set_language("en")
    book.add_author(book_author)

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
.thesis {
    background: #f5f5f0;
    border-left: 3px solid #888;
    padding: 0.8em 1em;
    margin: 1em 0 1.5em 0;
    font-style: italic;
}
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 2em 0;
}
"""
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=css_content.encode("utf-8"),
    )
    book.add_item(css)

    # Split the coherence-pass output back into chapters by <h2> or <hr>
    final_soup = BeautifulSoup(final_html, "lxml")
    h2_tags = final_soup.find_all("h2")

    if h2_tags:
        # Split by h2 headings
        epub_chapters = []
        toc = []

        for i, h2 in enumerate(h2_tags, start=1):
            chap_title = h2.get_text(strip=True)
            parts = [str(h2)]
            for sibling in h2.find_next_siblings():
                if sibling.name == "h2":
                    break
                if sibling.name == "hr":
                    continue
                parts.append(str(sibling))

            body_html = "\n".join(parts)

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

        book.toc = toc
    else:
        # No h2 tags — put everything in one chapter
        chap = epub.EpubHtml(
            title=book_title,
            file_name="content.xhtml",
            lang="en",
        )
        chap.content = (
            f'<html><head><link rel="stylesheet" href="style/default.css" '
            f'type="text/css"/></head><body>{final_html}</body></html>'
        ).encode("utf-8")
        chap.add_item(css)
        book.add_item(chap)
        epub_chapters = [chap]
        book.toc = [epub.Link("content.xhtml", book_title, "ch1")]

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    epub_path = OUTPUT_DIR / f"{safe_title}_distilled.epub"
    epub.write_epub(str(epub_path), book)
    print(f"\nEPUB saved: {epub_path}")

    # HTML backup
    html_doc = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{book_title} — Distilled</title>\n"
        f"<style>{css_content}</style>\n"
        "</head>\n<body>\n"
        f"<h1>{book_title} (Distilled)</h1>\n"
        f"{final_html}\n"
        "</body>\n</html>"
    )
    html_path = OUTPUT_DIR / f"{safe_title}_distilled.html"
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"HTML saved: {html_path}")

    final_word_count = _count_words(final_html)
    print(f"\nFinal word count: {final_word_count:,}")

    return epub_path


# ===========================================================================
# EPUB-to-HTML conversion
# ===========================================================================
def convert_epub_to_html(epub_path: Path) -> Path:
    """Convert an EPUB file to a single HTML file in the source directory."""
    from ebooklib import epub as ep

    print(f"Converting EPUB to HTML: {epub_path.name}")
    book = ep.read_epub(str(epub_path))

    title = book.get_metadata("DC", "title")
    title = title[0][0] if title else epub_path.stem
    author = book.get_metadata("DC", "creator")
    author = author[0][0] if author else ""

    toc_map: dict[str, str] = {}
    for toc_entry in book.toc:
        if hasattr(toc_entry, "href") and hasattr(toc_entry, "title"):
            href = toc_entry.href.split("#")[0]
            if toc_entry.title:
                toc_map[href] = toc_entry.title
        elif isinstance(toc_entry, tuple) and len(toc_entry) == 2:
            section, children = toc_entry
            if hasattr(section, "href") and hasattr(section, "title"):
                href = section.href.split("#")[0]
                if section.title:
                    toc_map[href] = section.title
            for child in children:
                if hasattr(child, "href") and hasattr(child, "title"):
                    href = child.href.split("#")[0]
                    if child.title:
                        toc_map[href] = child.title

    if toc_map:
        print(f"  Found {len(toc_map)} TOC entries in EPUB")

    html_parts = [
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
    ]
    if author:
        html_parts.append(f'<meta name="author" content="{author}">\n')
    html_parts.append("</head>\n<body>\n")

    for item in book.get_items_of_type(9):
        soup = BeautifulSoup(item.get_content(), "lxml")
        body = soup.find("body")
        body_html = ""
        if body:
            body_html = "".join(str(c) for c in body.children)
        else:
            body_html = soup.get_text()

        item_filename = item.get_name().split("/")[-1]
        toc_title = toc_map.get(item.get_name()) or toc_map.get(item_filename)

        if toc_title:
            has_heading = bool(
                BeautifulSoup(body_html, "lxml").find(["h1", "h2", "h3", "h4"])
            )
            if not has_heading:
                html_parts.append(f"<h2>{toc_title}</h2>\n")

        html_parts.append(body_html)
        html_parts.append("\n")

    html_parts.append("</body>\n</html>")

    output_path = SOURCE_DIR / (epub_path.stem + ".html")
    output_path.write_text("".join(html_parts), encoding="utf-8")
    print(f"Converted to: {output_path.name}")
    return output_path


# ===========================================================================
# Master orchestration
# ===========================================================================
def find_source_file() -> Path:
    html_files = list(SOURCE_DIR.glob("*.html")) + list(SOURCE_DIR.glob("*.htm"))
    epub_files = list(SOURCE_DIR.glob("*.epub"))

    if html_files:
        if len(html_files) > 1:
            print(f"WARNING: Multiple HTML files found in {SOURCE_DIR}/:")
            for p in html_files:
                print(f"  {p.name}")
            print("Using the first one.")
        return html_files[0]

    if epub_files:
        if len(epub_files) > 1:
            print(f"WARNING: Multiple EPUB files found in {SOURCE_DIR}/:")
            for p in epub_files:
                print(f"  {p.name}")
            print("Using the first one.")
        return convert_epub_to_html(epub_files[0])

    print(f"ERROR: No .html or .epub files found in {SOURCE_DIR}/")
    print("Place your source ebook file in the source/ directory.")
    sys.exit(1)


def get_chapter_files() -> list[Path]:
    files = sorted(CHAPTERS_DIR.glob("*.html"))
    if not files:
        print(f"ERROR: No chapter files found in {CHAPTERS_DIR}/")
        print("Run stages 1-2 first (or run pipeline.py first).")
        sys.exit(1)
    return files


SELECTION_FILE = BASE_DIR / "selected_chapters.txt"


def filter_chapters(chapter_files: list[Path]) -> list[Path]:
    """Let the user select which chapters to process."""
    print("\n" + "=" * 60)
    print("Chapter Selection")
    print("=" * 60)

    if SELECTION_FILE.exists():
        saved = [
            line.strip()
            for line in SELECTION_FILE.read_text().splitlines()
            if line.strip()
        ]
        if saved:
            matched = [f for f in chapter_files if f.name in saved]
            if matched:
                print(f"\nFound saved selection ({len(matched)} chapters):")
                for f in matched:
                    print(f"  {f.name}")
                reuse = input("\nUse this selection? [Y/n] ").strip()
                if reuse.lower() != "n":
                    return matched
                print()

    print("\nAll chapters found:")
    for i, f in enumerate(chapter_files, 1):
        print(f"  {i:3d}. {f.stem}")

    print(
        "\nEnter chapter numbers to EXCLUDE (comma-separated), "
        "or press Enter to keep all."
    )
    print("Example: 1,2,3,4,5,6,7,8,9  to skip front matter")
    exclude_input = input("\nExclude: ").strip()

    if not exclude_input:
        selected = chapter_files
    else:
        try:
            exclude_nums = {
                int(x.strip()) for x in exclude_input.split(",") if x.strip()
            }
        except ValueError:
            print("Invalid input. Keeping all chapters.")
            exclude_nums = set()

        selected = [
            f
            for i, f in enumerate(chapter_files, 1)
            if i not in exclude_nums
        ]

    SELECTION_FILE.write_text(
        "\n".join(f.name for f in selected) + "\n"
    )

    print(f"\nSelected {len(selected)} chapters for distilling:")
    for f in selected:
        print(f"  {f.name}")

    return selected


def main():
    parser = argparse.ArgumentParser(
        description="Ebook Distiller — Hierarchical Summarization Pipeline"
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
    parser.add_argument(
        "--target",
        type=int,
        default=10000,
        help="Target total word count (default: 10000)",
    )
    args = parser.parse_args()

    start_stage = args.stage
    dry_run = args.dry_run
    target_words = args.target

    print("=" * 60)
    print("  Ebook Distiller — Hierarchical Summarization")
    print("=" * 60)
    if dry_run:
        print("  Mode: DRY RUN (stages 1-2 only)")
    print(f"  Starting from stage: {start_stage}")
    print(f"  Target word count: {target_words:,}")

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
            chapter_tag = stage1(html_path)
        chapter_files = stage2(html_path, chapter_tag)
    else:
        print(f"Skipping Stage 2 (starting from stage {start_stage})")
        chapter_files = get_chapter_files()

    # Filter chapters
    chapter_files = filter_chapters(chapter_files)

    if dry_run:
        # Show what the proportional targets would be
        total_words = 0
        for fp in chapter_files:
            total_words += _count_words(fp.read_text(encoding="utf-8"))
        print(f"\nTotal source words: {total_words:,}")
        print(f"Target: {target_words:,} words")
        print(f"Compression ratio: ~{total_words / max(target_words, 1):.1f}:1")
        print("\n" + "=" * 60)
        print("DRY RUN COMPLETE — Stages 1-2 finished.")
        print("=" * 60)
        return

    # Stage 3 — Pass 1
    if start_stage <= 3:
        results = stage3(chapter_files, target_words)
    else:
        print(f"Skipping Stage 3 (starting from stage {start_stage})")

    # Stage 4
    if start_stage <= 4:
        if results is None:
            results = []
        if results:
            stage4(results, chapter_files)
        else:
            print("\nStage 4: Validating existing distilled files…")
            expected = {fp.stem + "_distilled.html" for fp in chapter_files}
            actual = {
                p.name
                for p in DISTILLED_DIR.iterdir()
                if p.suffix == ".html"
            } if DISTILLED_DIR.exists() else set()
            missing = expected - actual
            if missing:
                print(f"  WARNING: {len(missing)} distilled file(s) missing:")
                for m in sorted(missing):
                    print(f"    {m}")
            else:
                print(f"  All {len(expected)} distilled chapters present.")

    # Stage 5 — Pass 2 + assembly
    if start_stage <= 5:
        stage5(chapter_files, target_words)

    print("\n" + "=" * 60)
    print("  Distiller pipeline complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
