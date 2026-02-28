#!/usr/bin/env python3
"""
Prepare — Convert an ASCM download ticket to a clean, DRM-free EPUB.

Takes an .ascm/.acsm file (the download ticket from ebooks.com or other
Adobe DRM retailers), downloads the encrypted EPUB via Adobe's ADEPT
protocol, removes DRM, validates the result, and outputs a clean EPUB
ready for splitready.py.

Usage:
    python3 prepare.py mybook.acsm
    python3 prepare.py mybook.acsm --output ~/Desktop/bookprocessing/
    python3 prepare.py --setup
"""

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

ASCM_EXTENSIONS = {".ascm", ".acsm"}

# Common locations for knock credentials
KNOCK_CONFIG_DIRS = [
    Path.home() / ".config" / "knock",
    Path.home() / ".knock",
]

# Common locations for Adobe Digital Editions data
ADE_DATA_DIRS = [
    Path.home() / ".adobe-digital-editions",
    Path.home() / "Library" / "Application Support" / "Adobe Digital Editions",
]


# ===================================================================
# Helpers
# ===================================================================
def format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def slugify(text: str) -> str:
    """Convert a title to a filename-safe slug."""
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "_", text).strip("_").lower()
    return text


# ===================================================================
# ASCM parsing
# ===================================================================
def parse_ascm(ascm_path: Path) -> dict:
    """Parse an ASCM file and extract display metadata.

    ASCM (Adobe Content Server Message) files are XML documents
    containing a download ticket for an ADEPT-protected EPUB.
    """
    try:
        tree = ET.parse(ascm_path)
    except ET.ParseError:
        print(f"ERROR: Could not parse {ascm_path.name} as XML.")
        print("This doesn't look like an ASCM file. Did you download the")
        print("EPUB directly? If so, skip prepare.py and use splitready.py:")
        print(f"  python3 splitready.py {ascm_path.name}")
        sys.exit(1)

    root = tree.getroot()

    # Adobe ADEPT namespace — try common variants
    info = {}
    ns_prefixes = [
        "http://ns.adobe.com/adept",
        "http://ns.adobe.com/adept/",
    ]

    # Strip namespace from tags for simpler matching
    tag_text = {}
    for elem in root.iter():
        local_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if elem.text and elem.text.strip():
            tag_text[local_tag.lower()] = elem.text.strip()

    info["server"] = tag_text.get("fulfillmentserver", "")
    info["resource_id"] = (
        tag_text.get("resourceid", "")
        or tag_text.get("resource", "")
    )

    return info


# ===================================================================
# knock detection
# ===================================================================
def find_knock_cli() -> str | None:
    """Return the knock CLI command name if available, else None."""
    for cmd in ["knock", "knock-drm"]:
        try:
            result = subprocess.run(
                [cmd, "--help"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 or "usage" in (result.stdout + result.stderr).lower():
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def import_knock_library():
    """Try to import knock as a Python library. Returns the module or None."""
    try:
        import knock
        return knock
    except ImportError:
        pass

    try:
        import knock_drm as knock
        return knock
    except ImportError:
        pass

    return None


def check_knock() -> tuple[str, str | None]:
    """Check knock availability.

    Returns (mode, detail) where mode is 'cli', 'library', or 'missing'.
    For 'cli', detail is the command name.
    """
    cli_cmd = find_knock_cli()
    if cli_cmd:
        return ("cli", cli_cmd)

    lib = import_knock_library()
    if lib:
        return ("library", None)

    return ("missing", None)


def require_knock() -> tuple[str, str | None]:
    """Check knock is installed; exit with instructions if not."""
    mode, detail = check_knock()
    if mode == "missing":
        print("ERROR: knock is not installed.")
        print("Install it with:")
        print("  pip3 install knock")
        print()
        print("knock is a Python wrapper around libgourou that handles")
        print("Adobe ADEPT downloads and DRM removal.")
        sys.exit(1)
    return mode, detail


# ===================================================================
# Setup — first-time Adobe authorization
# ===================================================================
def check_authorized() -> bool:
    """Check if the machine has Adobe ADEPT credentials."""
    for d in KNOCK_CONFIG_DIRS:
        if d.exists() and any(d.iterdir()):
            return True
    return False


def run_setup(verbose: bool = False):
    """First-time setup: authorize this machine with Adobe's ADEPT server."""
    print("\n" + "=" * 60)
    print("  prepare.py — Adobe Account Setup")
    print("=" * 60)

    mode, cli_cmd = require_knock()

    print("\nThis will authorize your machine with Adobe's ADEPT server.")
    print("You need the same Adobe ID (email + password) you use for")
    print("Adobe Digital Editions.\n")

    # Check for existing knock credentials
    for d in KNOCK_CONFIG_DIRS:
        if d.exists() and any(d.iterdir()):
            print(f"Found existing credentials in {d}")
            reuse = input("Use existing authorization? [Y/n] ").strip()
            if reuse.lower() != "n":
                print("\nUsing existing credentials. Setup complete.")
                return
            break

    # Check if ADE credentials can be imported
    for d in ADE_DATA_DIRS:
        if d.exists():
            print(f"Found Adobe Digital Editions data in {d}")
            import_ade = input("Import credentials from ADE? [Y/n] ").strip()
            if import_ade.lower() != "n":
                if mode == "cli":
                    result = subprocess.run(
                        [cli_cmd, "import-ade"],
                        capture_output=not verbose, text=True,
                    )
                    if result.returncode == 0:
                        print("\nSuccessfully imported ADE credentials.")
                        print("Setup complete.")
                        return
                print("Import failed. Setting up new authorization instead.\n")
            break

    # Prompt for Adobe credentials
    email = input("Adobe ID (email): ").strip()
    if not email:
        print("ERROR: Adobe ID is required.")
        sys.exit(1)

    password = getpass.getpass("Adobe password: ")
    if not password:
        print("ERROR: Password is required.")
        sys.exit(1)

    print("\nAuthorizing with Adobe…")

    if mode == "cli":
        result = subprocess.run(
            [cli_cmd, "setup", "--email", email, "--password", password],
            capture_output=not verbose, text=True,
        )
        if result.returncode != 0:
            print("ERROR: Authorization failed.")
            if result.stderr:
                print(f"  {result.stderr.strip()}")
            print("\nCheck that your Adobe ID and password are correct.")
            print("If you don't have an Adobe ID, create one at:")
            print("  https://accounts.adobe.com")
            sys.exit(1)
    else:
        knock = import_knock_library()
        try:
            if hasattr(knock, "setup"):
                knock.setup(email=email, password=password)
            elif hasattr(knock, "authorize"):
                knock.authorize(email=email, password=password)
            else:
                print("ERROR: Could not find a setup function in knock library.")
                print("Try using knock from the command line instead:")
                print("  knock setup --email your@email.com --password yourpassword")
                sys.exit(1)
        except Exception as e:
            print(f"ERROR: Authorization failed: {e}")
            sys.exit(1)

    print("\nAuthorization successful.")
    print("You only need to do this once.\n")


# ===================================================================
# Download + DRM removal
# ===================================================================
def download_and_decrypt(
    ascm_path: Path, output_dir: Path, verbose: bool = False,
) -> Path:
    """Download EPUB from ASCM and remove DRM. Returns path to clean EPUB.

    Most knock versions handle both download and DRM removal in a single
    call. If knock only handles the download, falls back to the vendored
    DeDRM library for the decryption step.
    """
    mode, cli_cmd = require_knock()

    # Check authorization
    if not check_authorized():
        print("ERROR: This machine is not authorized with Adobe.")
        print("Run: python3 prepare.py --setup")
        sys.exit(1)

    # Parse and display ASCM info
    ascm_info = parse_ascm(ascm_path)

    print(f"\nReading ASCM: {ascm_path.name}")
    if ascm_info.get("server"):
        print(f"  License server: {ascm_info['server']}")
    if ascm_info.get("resource_id"):
        resource_display = ascm_info["resource_id"]
        if len(resource_display) > 40:
            resource_display = resource_display[:37] + "…"
        print(f"  Resource ID:    {resource_display}")

    print("\nDownloading from Adobe Content Server…")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if mode == "cli":
            clean_epub = _download_cli(
                cli_cmd, ascm_path, tmp_path, verbose,
            )
        else:
            clean_epub = _download_library(
                ascm_path, tmp_path, verbose,
            )

        # Generate output filename from EPUB metadata or ASCM name
        final_name = _make_filename(clean_epub, ascm_path)
        final_path = output_dir / final_name

        # Avoid overwriting without notice
        if final_path.exists():
            print(f"  Note: overwriting existing {final_name}")

        shutil.copy2(clean_epub, final_path)

    return final_path


def _download_cli(
    cli_cmd: str, ascm_path: Path, tmp_dir: Path, verbose: bool,
) -> Path:
    """Use knock CLI to download and decrypt."""
    # Most knock CLIs accept: knock <input.acsm> [--output <dir>]
    cmd = [cli_cmd, str(ascm_path), "--output", str(tmp_dir)]
    if verbose:
        cmd.append("--verbose")

    result = subprocess.run(
        cmd, capture_output=True, text=True,
    )

    # If --output isn't supported, retry without it (run in tmp_dir)
    if result.returncode != 0 and "--output" in (result.stderr or ""):
        cmd = [cli_cmd, str(ascm_path)]
        if verbose:
            cmd.append("--verbose")
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(tmp_dir),
        )

    if verbose and result.stdout:
        for line in result.stdout.strip().splitlines():
            print(f"  {line}")

    if result.returncode != 0:
        _handle_download_error(result.stderr or result.stdout)

    # Find the output EPUB
    output_epubs = list(tmp_dir.glob("*.epub"))

    # knock may also write next to the ASCM file
    if not output_epubs:
        ascm_dir = ascm_path.parent
        candidate = ascm_dir / (ascm_path.stem + ".epub")
        if candidate.exists():
            output_epubs = [candidate]

    if not output_epubs:
        print("ERROR: knock did not produce an output file.")
        print("Try running with --verbose for more details.")
        sys.exit(1)

    epub_path = output_epubs[0]
    size = format_size(epub_path.stat().st_size)
    print(f"  Received: {epub_path.name} ({size})")
    print("  DRM removed.")

    return epub_path


def _download_library(
    ascm_path: Path, tmp_dir: Path, verbose: bool,
) -> Path:
    """Use knock Python library to download and decrypt."""
    knock = import_knock_library()

    try:
        # Try all-in-one function first (download + decrypt)
        for fn_name in ("process_ascm", "fulfill", "download_and_decrypt"):
            fn = getattr(knock, fn_name, None)
            if fn:
                result_path = fn(str(ascm_path), str(tmp_dir))
                epub_path = Path(result_path)
                size = format_size(epub_path.stat().st_size)
                print(f"  Received: {epub_path.name} ({size})")
                print("  DRM removed.")
                return epub_path

        # Separate download step
        download_fn = getattr(knock, "download", None)
        if not download_fn:
            print("ERROR: Could not find a usable function in knock library.")
            print("Try using knock from the command line instead.")
            sys.exit(1)

        drm_epub = Path(download_fn(str(ascm_path), str(tmp_dir)))
        drm_size = format_size(drm_epub.stat().st_size)
        print(f"  Received: {drm_epub.name} ({drm_size}, Adobe ADEPT DRM)")

        # Separate decrypt step
        print("\nRemoving DRM…")
        for fn_name in ("remove_drm", "decrypt", "strip_drm"):
            fn = getattr(knock, fn_name, None)
            if fn:
                result_path = fn(str(drm_epub), str(tmp_dir))
                epub_path = Path(result_path)
                print("  Done.")
                return epub_path

        # Fall back to vendored DeDRM
        return _dedrm_fallback(drm_epub, tmp_dir)

    except SystemExit:
        raise
    except Exception as e:
        _handle_download_error(str(e))


def _dedrm_fallback(drm_epub: Path, output_dir: Path) -> Path:
    """Remove DRM using the vendored DeDRM library."""
    try:
        from dedrm import ineptepub
    except ImportError:
        print("ERROR: DRM removal requires either:")
        print("  1. A version of knock that handles DRM removal, or")
        print("  2. The DeDRM library:")
        print("     pip3 install git+https://github.com/noDRM/DeDRM_tools.git")
        print("  3. Or vendor DeDRM into the dedrm/ directory in this project")
        sys.exit(1)

    output_path = output_dir / f"{drm_epub.stem}_clean.epub"

    try:
        ineptepub.decryptBook(str(drm_epub), str(output_path))
    except Exception as e:
        print(f"ERROR: Could not remove DRM: {e}")
        print("The Adobe account used in --setup may not match the")
        print("account that purchased this book.")
        sys.exit(1)

    print("  Done.")
    return output_path


def _handle_download_error(error_text: str):
    """Exit with a helpful error message based on the failure."""
    error_lower = (error_text or "").lower()

    if "not authorized" in error_lower or "authorization" in error_lower:
        print("ERROR: This machine is not authorized with Adobe.")
        print("Run: python3 prepare.py --setup")
    elif "already fulfilled" in error_lower or "limit" in error_lower:
        print("ERROR: Adobe's server rejected the download.")
        print("This can happen if the ASCM has already been used")
        print("(ebooks.com limits downloads to 3 per format).")
        print("Check your ebooks.com account for re-download options.")
    elif "expired" in error_lower:
        print("ERROR: This ASCM download ticket has expired.")
        print("Request a new download from your ebooks.com account.")
    else:
        print("ERROR: Download failed.")
        if error_text and error_text.strip():
            print(f"  {error_text.strip()}")

    sys.exit(1)


# ===================================================================
# Validation
# ===================================================================
def validate_epub(epub_path: Path) -> dict:
    """Validate the EPUB and return metadata."""
    try:
        from ebooklib import epub
    except ImportError:
        print("  WARNING: ebooklib not installed, skipping validation.")
        return {}

    try:
        book = epub.read_epub(str(epub_path))
    except Exception as e:
        print(f"ERROR: DRM was removed but the EPUB appears corrupted.")
        print(f"  {e}")
        print("Try re-downloading from ebooks.com.")
        sys.exit(1)

    title_meta = book.get_metadata("DC", "title")
    title = title_meta[0][0] if title_meta else epub_path.stem

    author_meta = book.get_metadata("DC", "creator")
    author = author_meta[0][0] if author_meta else "Unknown"

    spine_items = len(book.spine)

    return {
        "title": title,
        "author": author,
        "spine_items": spine_items,
    }


# ===================================================================
# Output filename
# ===================================================================
def _make_filename(epub_path: Path, ascm_path: Path) -> str:
    """Generate a clean filename for the output EPUB."""
    # Try to derive from EPUB metadata
    try:
        from ebooklib import epub
        book = epub.read_epub(str(epub_path))
        title_meta = book.get_metadata("DC", "title")
        if title_meta and title_meta[0][0]:
            slug = slugify(title_meta[0][0])
            if slug:
                return f"{slug}.epub"
    except Exception:
        pass

    # Fall back to ASCM filename
    stem = ascm_path.stem
    slug = slugify(stem) or "book"
    return f"{slug}.epub"


# ===================================================================
# Main
# ===================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Convert an ASCM download ticket to a clean, DRM-free EPUB",
    )
    parser.add_argument(
        "ascm", nargs="?",
        help="Path to the .ascm/.acsm file (download ticket from ebooks.com)",
    )
    parser.add_argument(
        "--setup", action="store_true",
        help="First-time setup: authorize this machine with Adobe",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output directory or file path (default: current directory)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show ADEPT negotiation details",
    )
    parser.add_argument(
        "--then-split", action="store_true",
        help="Run splitready.py on the output EPUB automatically",
    )
    args = parser.parse_args()

    # --setup mode
    if args.setup:
        run_setup(verbose=args.verbose)
        return

    # Normal mode — require ASCM file
    if not args.ascm:
        parser.print_help()
        print("\nExamples:")
        print("  python3 prepare.py mybook.acsm")
        print("  python3 prepare.py mybook.acsm --output ~/Desktop/")
        print("  python3 prepare.py --setup")
        sys.exit(1)

    ascm_path = Path(args.ascm).resolve()

    # Validate input file
    if not ascm_path.exists():
        print(f"ERROR: File not found: {ascm_path}")
        sys.exit(1)

    if ascm_path.suffix.lower() not in ASCM_EXTENSIONS:
        print(f"ERROR: Expected an .ascm/.acsm file, got: {ascm_path.suffix}")
        print("This doesn't look like an ASCM file. Did you download the")
        print("EPUB directly? If so, skip prepare.py and use splitready.py:")
        print(f"  python3 splitready.py {ascm_path.name}")
        sys.exit(1)

    # Determine output directory
    if args.output:
        output = Path(args.output).resolve()
        if output.suffix.lower() == ".epub":
            output_dir = output.parent
        else:
            output_dir = output
    else:
        output_dir = Path.cwd()

    output_dir.mkdir(parents=True, exist_ok=True)

    # Header
    print("=" * 60)
    print("  prepare.py — ASCM to clean EPUB")
    print("=" * 60)

    # Download and decrypt
    clean_epub = download_and_decrypt(ascm_path, output_dir, verbose=args.verbose)

    # Validate
    print("\nValidating EPUB…")
    metadata = validate_epub(clean_epub)

    if metadata:
        print(f"  Title:    {metadata['title']}")
        print(f"  Author:   {metadata['author']}")
        print(f"  Chapters: {metadata['spine_items']} spine items")

    file_size = format_size(clean_epub.stat().st_size)
    print(f"  Size:     {file_size} (clean)")

    print(f"\nSaved: {clean_epub}")

    # Chain to splitready.py if requested
    if args.then_split:
        print(f"\nRunning splitready.py on {clean_epub.name}…")
        splitready = BASE_DIR / "splitready.py"
        if not splitready.exists():
            print(f"ERROR: splitready.py not found at {splitready}")
            sys.exit(1)
        result = subprocess.run(
            [sys.executable, str(splitready), str(clean_epub)],
        )
        sys.exit(result.returncode)
    else:
        print(f"\nNext step:")
        print(f"  python3 splitready.py {clean_epub.name}")
        print()


if __name__ == "__main__":
    main()
