# Book Processing

Three Python scripts that process ebooks through the Claude API, each producing a different kind of output:

| Script | What it does | Output size |
|---|---|---|
| `abridger.py` | **Abridger** — cuts 40-60% of the text while keeping the author's original words | ~50% of original |
| `outliner.py` | **Outliner** — thesis, outline, key concepts, key passages, and critical questions for each chapter | 30% of original |
| `distiller.py` | **Distiller** — two-pass hierarchical summarization that condenses a full book into a flowing summary | ~10,000 words (configurable) |
| `fiction_distiller.py` | **Fiction Distiller** — selective abridgment for novels: keeps original prose, dialogue, and voice while cutting to the scenes that matter | ~15,000 words (configurable) |

All three output a Kindle-compatible EPUB and a single HTML file.

Also includes `papercopyprep.py`, a utility that converts scanned/OCR'd PDFs into EPUBs so physical books can be processed too.

---

## Prerequisites

- **Python 3.10+** (included on Mac — check with `python3 --version`)
- **An Anthropic API key** — sign up at [console.anthropic.com](https://console.anthropic.com) and add credits under Settings > Billing

---

## Setup

### 1. Download the project

```bash
cd ~/Desktop
git clone https://github.com/benwstevens/bookprocessing.git
cd bookprocessing
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

This installs: `anthropic`, `ebooklib`, `beautifulsoup4`, `lxml`, `pymupdf`, `knock`

### 3. Add your API key

Create a file called `.env` in the `bookprocessing` folder:

```bash
nano .env
```

Type this single line (paste your actual key after the `=`):

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Save with **Ctrl+O**, **Enter**, **Ctrl+X**.

> **Note:** The `.env` file is hidden in Finder because it starts with a dot. That's normal. It won't be uploaded to GitHub.

### 4. Add your ebook

Drop your `.epub` file into the `bookprocessing` folder (alongside the `.py` files). All three scripts accept EPUB files directly. If you only have a physical copy, see [Processing a physical book](#processing-a-physical-book) below.

---

## Usage

Open Terminal, navigate to the project folder, and run any script:

```bash
cd ~/Desktop/bookprocessing
```

### Outliner (outliner.py)

Produces a detailed chapter-by-chapter analysis: thesis, outline, key concepts, key passages, and critical questions.

```bash
python3 outliner.py mybook.epub
```

### Abridger (abridger.py)

Aggressively cuts 40-60% of each chapter while preserving the author's exact words and argument structure.

```bash
python3 abridger.py mybook.epub
```

### Distiller (distiller.py)

Two-pass summarization: first summarizes each chapter proportionally, then runs a coherence pass to produce a single flowing document.

```bash
python3 distiller.py mybook.epub
```

To change the target word count (default is 10,000):

```bash
python3 distiller.py mybook.epub --target 5000
```

### Fiction Distiller (fiction_distiller.py)

Selective abridgment for fiction. Keeps the author's original prose, dialogue, and voice — cuts to the scenes that matter and bridges gaps with minimal italicized narration. The output reads like the actual novel, just shorter.

```bash
python3 fiction_distiller.py mynovel.epub
```

To change the target word count (default is 15,000):

```bash
python3 fiction_distiller.py mynovel.epub --target 20000
```

### Options (all scripts)

| Flag | What it does |
|---|---|
| `--stage N` | Restart from stage N (useful if a run was interrupted) |
| `--dry-run` | Run stages 1-2 only — splits chapters and shows cost estimate, no API calls |

Example:

```bash
python3 distiller.py mybook.epub --dry-run      # Preview chapters and cost
python3 distiller.py mybook.epub --stage 3       # Re-run API calls only
```

---

## Processing a physical book

If a book only exists in print, you can scan it and convert it to EPUB:

### 1. Scan the book

Cut the spine (or use a V-shaped book scanner) and feed the pages through a document scanner. Most scanners (like ScanSnap) output a searchable PDF with OCR built in.

### 2. OCR (if your scanner didn't do it)

If your PDF is image-only (you can't select text in it), run OCR first:

```bash
brew install ocrmypdf          # One-time install
ocrmypdf scanned.pdf searchable.pdf
```

### 3. Convert to EPUB

```bash
python3 papercopyprep.py searchable.pdf
```

This extracts the text, detects chapter boundaries, cleans up OCR artifacts (broken hyphens, stray page numbers, bad spacing), and outputs an EPUB file.

You can also specify the title and author up front:

```bash
python3 papercopyprep.py searchable.pdf --title "The Book Title" --author "Author Name"
```

The output EPUB appears in the same folder as the PDF. From there, use it like any other EPUB:

```bash
python3 outliner.py "The_Book_Title.epub"
```

> **Note:** OCR quality varies. If the scan is blurry or the book uses unusual fonts, you may see garbled text. Check the EPUB output before running it through the processing scripts.

---

## Processing a purchased ebook

If you buy DRM-protected ebooks from ebooks.com (or other Adobe DRM retailers):

### First time only: link your Adobe account

```bash
python3 prepare.py --setup
```

This connects your Adobe ID to your machine (same as Adobe Digital Editions does on first launch). You only need to do this once.

### For each book

```bash
python3 prepare.py ~/Downloads/URLLink.acsm
```

This downloads the ebook and removes DRM, producing a clean EPUB you can process:

```bash
python3 splitready.py The_Great_Transformation.epub
python3 distiller.py The_Great_Transformation.epub
```

> **Note:** ebooks.com limits downloads to 3 per format. Each time you run
> prepare.py on the same ASCM file, it uses one of those downloads. The ASCM
> file is a one-time download ticket, not the book itself.

---

## How it works

Each script follows the same five stages:

1. **Detect** — Identifies which HTML heading tag marks chapter boundaries
2. **Split** — Splits the ebook into individual chapter files and asks you which to include (you can exclude front matter, part dividers, etc.)
3. **Process** — Sends each chapter to the Claude API with custom instructions
4. **Save** — Writes each processed chapter to disk (skips chapters already done, so interrupted runs resume automatically)
5. **Assemble** — Combines everything into a final EPUB and HTML file

### Chapter selection

When you run a script for the first time on a book, it will list all detected chapters and ask which to exclude. You can type:

- `1-9` — exclude chapters 1 through 9
- `1-5,22,23` — exclude a mix of ranges and individual numbers
- Press **Enter** — keep all chapters

Your selection is saved so you won't be asked again on re-runs.

---

## Output

Each book gets its own folder automatically:

```
books/
  mybook/
    source/                      # Original EPUB + converted HTML
    chapters/                    # Individual chapter HTML files
    chapter_summaries/           # Study companion output (outliner.py)
    abridged_chapters/           # Abridged output (abridger.py)
    distilled_chapters/          # Distilled output (distiller.py)
    fiction_distilled_chapters/  # Fiction distilled output (fiction_distiller.py)
    output/                      # Final EPUB and HTML files
```

You never need to delete files between books — just run a script with a different EPUB and it creates a new folder.

---

## Cost

These scripts use the Claude API, which charges per token. Rough estimates for a 100,000-word book:

| Script | Estimated cost |
|---|---|
| Outliner | ~$3-6 |
| Abridger | ~$5-10 |
| Distiller | ~$4-8 |
| Fiction Distiller | ~$5-10 |

Each script shows an estimated cost and asks for confirmation before making any API calls. You can check your balance and add credits at [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).

---

## Customizing the instructions

Each script reads its Claude instructions from a plain text file:

| Script | Instructions file |
|---|---|
| `outliner.py` | `outliner_instructions.txt` |
| `abridger.py` | `abridger_instructions.txt` |
| `distiller.py` | `distiller_instructions.txt` + `distiller_coherence_instructions.txt` |
| `fiction_distiller.py` | `fiction_distiller_instructions.txt` + `fiction_distiller_coherence_instructions.txt` |

Edit these files to change what Claude produces. The only hard requirement is that the output must be HTML (not Markdown) — the scripts expect HTML when assembling the final EPUB.

---

## Troubleshooting

**"ANTHROPIC_API_KEY not found"**
Your `.env` file is missing or doesn't contain the key. See [Setup step 3](#3-add-your-api-key).

**"No heading tags found"**
The ebook uses an unusual format. Try a different EPUB source, or open an issue.

**"Out of credits" / 402 error**
Add credits at [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).

**Script interrupted mid-run**
Just re-run the same command. It skips chapters that already have output files and picks up where it left off.

**"Streaming is required" error**
This should be resolved in the current version. If you see it, run `git pull` to get the latest code.

**Mac falls asleep during a long run**
Open System Settings > Displays > Advanced > uncheck "Automatically turn off display" while processing. Or run `caffeinate -i python3 distiller.py mybook.epub` to prevent sleep for that command.

---

## Project structure

```
bookprocessing/
  prepare.py                                 # ASCM-to-clean-EPUB acquisition script
  outliner.py                                # Outliner script (study companion)
  abridger.py                                # Abridger script
  distiller.py                               # Distiller script
  fiction_distiller.py                        # Fiction distiller script
  papercopyprep.py                           # PDF-to-EPUB converter for scanned books
  shared.py                                  # Shared utilities (used by all scripts)
  dedrm/                                     # Vendored DeDRM library (optional)
  outliner_instructions.txt                  # Instructions for outliner
  abridger_instructions.txt                  # Instructions for abridger
  distiller_instructions.txt                 # Instructions for distiller (per-chapter pass)
  distiller_coherence_instructions.txt       # Instructions for distiller (coherence pass)
  fiction_distiller_instructions.txt          # Instructions for fiction distiller (per-chapter pass)
  fiction_distiller_coherence_instructions.txt  # Instructions for fiction distiller (coherence pass)
  requirements.txt                           # Python dependencies
  .env                                       # Your API key (not tracked by git)
  .gitignore
  README.md
```
