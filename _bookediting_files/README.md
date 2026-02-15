# Book Editing

Two Python scripts that send a manuscript (as EPUB) through the Claude API for editorial processing:

| Script | What it does | Output |
|---|---|---|
| `copyeditor.py` | **Copyeditor** — line-level editing: grammar, clarity, awkward phrasing, redundancy. Returns corrected text + change log. | Full corrected manuscript |
| `feedback.py` | **Editorial Feedback** — developmental feedback: argument strength, structure, pacing, clarity, reader engagement. Does not modify the text. | Feedback report per chapter |

Both output a Kindle-compatible EPUB and a single HTML file.

---

## Prerequisites

- **Python 3.10+** (included on Mac — check with `python3 --version`)
- **An Anthropic API key** — sign up at [console.anthropic.com](https://console.anthropic.com) and add credits under Settings > Billing

---

## Setup

### 1. Download the project

```bash
cd ~/Desktop
git clone https://github.com/benwstevens/bookediting.git
cd bookediting
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Add your API key

Create a file called `.env` in the `bookediting` folder:

```bash
nano .env
```

Type this single line (paste your actual key after the `=`):

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Save with **Ctrl+O**, **Enter**, **Ctrl+X**.

> **Note:** The `.env` file is hidden in Finder because it starts with a dot. That's normal. It won't be uploaded to GitHub.

### 4. Add your manuscript

Drop your `.epub` file into the `bookediting` folder (alongside the `.py` files).

---

## Usage

Open Terminal, navigate to the project folder, and run either script:

```bash
cd ~/Desktop/bookediting
```

### Copyeditor (copyeditor.py)

Line-level editing. Returns the full corrected chapter text plus a change log explaining every edit.

```bash
python3 copyeditor.py mymanuscript.epub
```

### Editorial Feedback (feedback.py)

Developmental feedback on argument, structure, pacing, and clarity. Does not rewrite your text — gives you notes to act on.

```bash
python3 feedback.py mymanuscript.epub
```

### Options (both scripts)

| Flag | What it does |
|---|---|
| `--stage N` | Restart from stage N (useful if a run was interrupted) |
| `--dry-run` | Run stages 1-2 only — splits chapters and shows cost estimate, no API calls |

Example:

```bash
python3 copyeditor.py mymanuscript.epub --dry-run     # Preview chapters and cost
python3 copyeditor.py mymanuscript.epub --stage 3     # Re-run API calls only
```

---

## How it works

Each script follows the same five stages:

1. **Detect** — Identifies which HTML heading tag marks chapter boundaries
2. **Split** — Splits the manuscript into individual chapter files and asks you which to include
3. **Process** — Sends each chapter to the Claude API with custom instructions
4. **Save** — Writes each processed chapter to disk (skips chapters already done, so interrupted runs resume automatically)
5. **Assemble** — Combines everything into a final EPUB and HTML file

### Chapter selection

When you run a script for the first time on a manuscript, it will list all detected chapters and ask which to exclude. You can type:

- `1-3` — exclude chapters 1 through 3
- `1,2,15-18` — exclude a mix of ranges and individual numbers
- Press **Enter** — keep all chapters

Your selection is saved so you won't be asked again on re-runs.

---

## Output

Each manuscript gets its own folder automatically:

```
books/
  mymanuscript/
    source/                # Original EPUB + converted HTML
    chapters/              # Individual chapter HTML files
    copyedited_chapters/   # Copyedited output (copyeditor.py)
    feedback/              # Editorial feedback (feedback.py)
    output/                # Final EPUB and HTML files
```

You never need to delete files between manuscripts — just run a script with a different EPUB and it creates a new folder.

---

## Cost

These scripts use the Claude API, which charges per token. Rough estimates for a 100,000-word manuscript:

| Script | Estimated cost |
|---|---|
| Copyeditor | ~$5-10 |
| Feedback | ~$3-6 |

Each script shows an estimated cost and asks for confirmation before making any API calls.

---

## Customizing the instructions

Each script reads its Claude instructions from a plain text file:

| Script | Instructions file |
|---|---|
| `copyeditor.py` | `copyeditor_instructions.txt` |
| `feedback.py` | `feedback_instructions.txt` |

Edit these files to change what Claude focuses on. For example, you could modify `feedback_instructions.txt` to focus specifically on pacing, or add a section on dialogue. The only hard requirement is that the output must be HTML (not Markdown).

---

## Adding new editing passes

To create a new editing pass (e.g., fact-checking, dialogue review, sensitivity reading):

1. Copy `feedback.py` as a starting point and rename it (e.g., `factchecker.py`)
2. Write a new instructions file (e.g., `factchecker_instructions.txt`)
3. Update the script to point to your new instructions file and output directory
4. Add the new output directory to `shared.py`'s `setup_book_dir()` function

---

## Troubleshooting

**"ANTHROPIC_API_KEY not found"**
Your `.env` file is missing or doesn't contain the key. See Setup step 3.

**"No heading tags found"**
The manuscript EPUB uses an unusual format. Try exporting from your writing tool with a different template.

**"Out of credits" / 402 error**
Add credits at [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).

**Script interrupted mid-run**
Just re-run the same command. It skips chapters that already have output files and picks up where it left off.

**Mac falls asleep during a long run**
Run with: `caffeinate -i python3 copyeditor.py mymanuscript.epub`

---

## Project structure

```
bookediting/
  copyeditor.py                  # Copyediting script
  feedback.py                    # Editorial feedback script
  shared.py                      # Shared utilities (used by both)
  copyeditor_instructions.txt    # Instructions for copyeditor
  feedback_instructions.txt      # Instructions for feedback
  requirements.txt               # Python dependencies
  .env                           # Your API key (not tracked by git)
  .gitignore
  README.md
```
