#!/usr/bin/env python3
"""
Paper Copy Prep — Convert a scanned/OCR'd PDF to an EPUB for processing.

Takes a searchable PDF (one that has been OCR'd), extracts the text,
detects chapter boundaries, and outputs a structured EPUB that can be
fed into outliner.py, abridger.py, or distiller.py.

Usage:
    python3 papercopyprep.py mybook.pdf
    python3 papercopyprep.py mybook.pdf --title "Book Title" --author "Author Name"
"""

import argparse
import re
import sys
from pathlib import Path

# Chapter heading patterns — ordered from most to least specific
CHAPTER_PATTERNS = [
    # "Chapter 1", "Chapter One", "CHAPTER 1", "CHAPTER ONE", etc.
    re.compile(
        r"^chapter\s+"
        r"(one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|\d+)"
        r"(\s*[:\.\-—]\s*.+)?$",
        re.IGNORECASE,
    ),
    # "Part 1", "Part One", "PART I", etc.
    re.compile(
        r"^part\s+"
        r"(one|two|three|four|five|six|seven|eight|nine|ten|"
        r"i{1,3}|iv|vi{0,3}|ix|x{0,3}|\d+)"
        r"(\s*[:\.\-—]\s*.+)?$",
        re.IGNORECASE,
    ),
    # Roman numerals alone on a line: "I", "II", "III", "IV", etc.
    re.compile(
        r"^(I{1,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XVI{0,3}|XIX|XXI{0,3})$"
    ),
    # Just a number alone on a line: "1", "2", etc. (common in some books)
    re.compile(r"^\d{1,3}$"),
]

# Common front/back matter headings
MATTER_HEADINGS = {
    "contents", "table of contents", "acknowledgments", "acknowledgements",
    "bibliography", "notes", "endnotes", "index", "about the author",
    "introduction", "preface", "foreword", "prologue", "epilogue",
    "afterword", "appendix", "glossary", "dedication",
}


def extract_text_from_pdf(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract text from each page of a PDF. Returns list of (page_num, text)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("ERROR: PyMuPDF is required. Install it with:")
        print("  pip3 install pymupdf")
        sys.exit(1)

    doc = fitz.open(str(pdf_path))
    pages = []

    print(f"Extracting text from {doc.page_count} pages…")

    empty_count = 0
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if text.strip():
            pages.append((i + 1, text))
        else:
            empty_count += 1

    if empty_count > doc.page_count * 0.5:
        print(f"\nWARNING: {empty_count} of {doc.page_count} pages have no text.")
        print("This PDF may not have been OCR'd yet.")
        print("Run OCR first: ocrmypdf input.pdf output.pdf")
        print("  (install with: brew install ocrmypdf)")
        sys.exit(1)

    if not pages:
        print("ERROR: No text found in PDF. The file may be image-only.")
        print("Run OCR first: ocrmypdf input.pdf output.pdf")
        sys.exit(1)

    print(f"  Extracted text from {len(pages)} pages ({empty_count} empty pages skipped)")
    return pages


def detect_chapters(pages: list[tuple[int, str]]) -> list[dict]:
    """Detect chapter boundaries from extracted page text.

    Returns a list of chapters, each with:
        title: str, pages: list of (page_num, text)
    """
    # Combine all text to analyze line-by-line
    all_lines = []
    for page_num, text in pages:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                all_lines.append((page_num, stripped))

    # First pass: find chapter markers
    chapter_markers = []  # (index_in_all_lines, title, page_num)

    for i, (page_num, line) in enumerate(all_lines):
        # Skip very long lines — chapter titles are short
        if len(line) > 100:
            continue

        # Check against chapter patterns
        for pattern in CHAPTER_PATTERNS:
            if pattern.match(line):
                # Look ahead for a subtitle on the next line
                title = line
                if i + 1 < len(all_lines):
                    next_line = all_lines[i + 1][1]
                    # If next line is short and on same/next page, it might be a subtitle
                    if (len(next_line) < 80 and
                            not any(p.match(next_line) for p in CHAPTER_PATTERNS)):
                        title = f"{line}: {next_line}"
                chapter_markers.append((i, title, page_num))
                break

        # Check for common section headings
        if line.lower() in MATTER_HEADINGS:
            chapter_markers.append((i, line, page_num))

    if not chapter_markers:
        print("\nWARNING: Could not detect chapter boundaries automatically.")
        print("The entire text will be treated as a single chapter.")
        print("You may want to edit the EPUB afterward to add chapter breaks.")
        full_text = "\n\n".join(text for _, text in pages)
        return [{"title": "Full Text", "text": full_text}]

    print(f"\n  Detected {len(chapter_markers)} chapter/section boundaries:")
    for _, title, page_num in chapter_markers:
        display_title = title[:60] + "…" if len(title) > 60 else title
        print(f"    p.{page_num}: {display_title}")

    # Second pass: collect text between chapter markers
    chapters = []
    page_text_map = {}
    for page_num, text in pages:
        page_text_map[page_num] = text

    # Build a flat text stream with page boundaries marked
    full_lines = []
    for page_num, text in pages:
        for line in text.splitlines():
            full_lines.append((page_num, line))

    for idx, (marker_pos, title, _) in enumerate(chapter_markers):
        # Collect text from this marker to the next
        if idx + 1 < len(chapter_markers):
            end_pos = chapter_markers[idx + 1][0]
        else:
            end_pos = len(all_lines)

        # Gather the text (skip the title line itself)
        chapter_text_parts = []
        for j in range(marker_pos + 1, end_pos):
            if j < len(all_lines):
                chapter_text_parts.append(all_lines[j][1])

        chapters.append({
            "title": title,
            "text": "\n".join(chapter_text_parts),
        })

    # If there's text before the first chapter marker, add it as front matter
    if chapter_markers and chapter_markers[0][0] > 0:
        front_parts = []
        for j in range(chapter_markers[0][0]):
            front_parts.append(all_lines[j][1])
        front_text = "\n".join(front_parts)
        if len(front_text.split()) > 50:  # Only if substantial
            chapters.insert(0, {
                "title": "Front Matter",
                "text": front_text,
            })

    return chapters


def clean_text(text: str) -> str:
    """Clean up OCR artifacts in extracted text."""
    # Fix common OCR issues
    # Double spaces
    text = re.sub(r"  +", " ", text)
    # Hyphenated line breaks (word- \nbreak → wordbreak)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    # Single newlines within paragraphs → spaces
    # (but preserve double newlines as paragraph breaks)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Fix spaces before punctuation
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    # Strip page numbers that appear alone on a line
    text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)

    return text.strip()


def text_to_html(text: str) -> str:
    """Convert plain text to HTML paragraphs."""
    paragraphs = re.split(r"\n\s*\n", text)
    html_parts = []
    for para in paragraphs:
        para = para.strip()
        if para:
            # Escape HTML entities
            para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_parts.append(f"<p>{para}</p>")
    return "\n".join(html_parts)


def build_epub(chapters: list[dict], title: str, author: str, output_path: Path):
    """Build an EPUB from chapter data."""
    try:
        from ebooklib import epub
    except ImportError:
        print("ERROR: ebooklib is required. Install it with:")
        print("  pip3 install ebooklib")
        sys.exit(1)

    book = epub.EpubBook()
    book.set_identifier("pdf-to-epub-" + re.sub(r"\W+", "-", title.lower()))
    book.set_title(title)
    book.set_language("en")
    if author:
        book.add_author(author)

    css_content = """
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.6; margin: 1em; color: #222; }
h1, h2, h3 { margin-top: 1.5em; margin-bottom: 0.5em; line-height: 1.2; }
p { margin-bottom: 0.8em; text-align: justify; }
"""
    css = epub.EpubItem(
        uid="style", file_name="style/default.css",
        media_type="text/css", content=css_content.encode("utf-8"),
    )
    book.add_item(css)

    epub_chapters = []
    toc = []
    total_words = 0

    for i, chapter in enumerate(chapters, start=1):
        cleaned = clean_text(chapter["text"])
        body_html = text_to_html(cleaned)
        word_count = len(cleaned.split())
        total_words += word_count

        chap_title = chapter["title"]
        full_html = f"<h2>{chap_title}</h2>\n{body_html}"

        chap = epub.EpubHtml(
            title=chap_title, file_name=f"chapter_{i:02d}.xhtml", lang="en",
        )
        chap.content = (
            f'<html><head><link rel="stylesheet" href="style/default.css" '
            f'type="text/css"/></head><body>{full_html}</body></html>'
        ).encode("utf-8")
        chap.add_item(css)
        book.add_item(chap)
        epub_chapters.append(chap)
        toc.append(epub.Link(f"chapter_{i:02d}.xhtml", chap_title, f"ch{i}"))

        print(f"  Chapter {i}: {chap_title} ({word_count:,} words)")

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    epub.write_epub(str(output_path), book)

    print(f"\nTotal: {total_words:,} words across {len(chapters)} chapters")
    print(f"EPUB saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert a scanned/OCR'd PDF to an EPUB for processing",
    )
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--title", help="Book title (auto-detected if not provided)")
    parser.add_argument("--author", help="Author name", default="")
    parser.add_argument(
        "--output", "-o",
        help="Output EPUB path (default: same name as PDF with .epub extension)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)
    if pdf_path.suffix.lower() != ".pdf":
        print(f"ERROR: Expected a .pdf file, got: {pdf_path.suffix}")
        sys.exit(1)

    print("=" * 60)
    print("  Paper Copy Prep — PDF to EPUB")
    print("=" * 60)
    print(f"  Input: {pdf_path.name}")

    # Extract text
    pages = extract_text_from_pdf(pdf_path)

    # Detect chapters
    chapters = detect_chapters(pages)

    # Get title
    title = args.title
    if not title:
        title = pdf_path.stem.replace("_", " ").replace("-", " ").title()
        user_title = input(f"\nBook title [{title}]: ").strip()
        if user_title:
            title = user_title

    author = args.author
    if not author:
        author = input("Author name (press Enter to skip): ").strip()

    # Build EPUB
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = pdf_path.with_suffix(".epub")

    print(f"\nBuilding EPUB: {output_path.name}")
    build_epub(chapters, title, author, output_path)

    print("\n" + "=" * 60)
    print("  Done! You can now run any processing script on it:")
    print(f"  python3 outliner.py {output_path.name}")
    print(f"  python3 abridger.py {output_path.name}")
    print(f"  python3 distiller.py {output_path.name}")
    print("=" * 60)


if __name__ == "__main__":
    main()
