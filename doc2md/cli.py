"""doc2md command line interface.

    doc2md                    numbered picker over convertible files here
    doc2md FILE [FILE...]     convert each file
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from .extract import ExtractionError, extract, is_supported, supported_extensions
from .model import slugify
from .render import check_anchors, write_document

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CHECK_FAILED = 2

# Everything both tools produce lands here, so converted documents never mix
# with the sources they came from.
OUTPUT_DIRNAME = "outputs"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


# --- picker ----------------------------------------------------------------


def find_convertible(directory: Path) -> list[Path]:
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and is_supported(p)),
        key=lambda p: p.name.lower(),
    )


def parse_selection(raw: str, count: int) -> list[int]:
    """Parse `3`, `1,4,5`, `2-6`, or `all` into zero-based indices.

    Raises ValueError with a user-facing message on bad input.
    """
    raw = raw.strip().lower()
    if raw in {"a", "all", "*"}:
        return list(range(count))

    chosen: list[int] = []
    for part in (p.strip() for p in raw.replace(" ", ",").split(",")):
        if not part:
            continue
        if "-" in part[1:]:
            lo_s, _, hi_s = part.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                raise ValueError(f"not a range: {part!r}")
            if lo > hi:
                lo, hi = hi, lo
            candidates = range(lo, hi + 1)
        else:
            try:
                candidates = [int(part)]
            except ValueError:
                raise ValueError(f"not a number: {part!r}")

        for n in candidates:
            if not 1 <= n <= count:
                raise ValueError(f"{n} is out of range (1-{count})")
            if n - 1 not in chosen:
                chosen.append(n - 1)
    return chosen


def pick_files(directory: Path) -> list[Path]:
    """Show a numbered menu and return the selected files (possibly empty)."""
    files = find_convertible(directory)
    if not files:
        exts = ", ".join(sorted(supported_extensions()))
        print(f"No convertible files in {directory}.", file=sys.stderr)
        print(f"Looking for: {exts}", file=sys.stderr)
        return []

    width = len(str(len(files)))
    print(f"Convertible files in {directory}:\n")
    for i, path in enumerate(files, 1):
        size = _human_size(path.stat().st_size)
        print(f"  {i:>{width}}. {path.name}  ({size})")
    print("\nSelect: a number, a list (1,3,5), a range (2-6), or 'all'. Enter to cancel.")

    try:
        raw = input("> ")
    except (EOFError, KeyboardInterrupt):
        print()
        return []

    if not raw.strip():
        return []

    try:
        return [files[i] for i in parse_selection(raw, len(files))]
    except ValueError as exc:
        print(f"doc2md: {exc}", file=sys.stderr)
        return []


# --- conversion ------------------------------------------------------------


def _claim_dest(outdir: Path, slug: str, source: Path, claimed: set[Path]) -> Path:
    """Pick an output directory, avoiding collisions inside this run.

    Two different sources can share a document title (a .docx and its .odt
    export, say). Disambiguating only against directories claimed earlier in
    the same run keeps re-running the same file idempotent, so the
    already-exists guard still fires as expected.
    """
    dest = outdir / slug
    if dest not in claimed:
        claimed.add(dest)
        return dest

    stem = slugify(source.stem, "document")
    candidate = outdir / f"{slug}-{stem}"
    counter = 2
    while candidate in claimed:
        candidate = outdir / f"{slug}-{stem}-{counter}"
        counter += 1
    claimed.add(candidate)
    return candidate


def convert_one(path: Path, outdir: Path, cfg, args, claimed: set[Path]) -> int:
    """Convert a single file. Returns an exit code."""
    try:
        doc = extract(path, cfg)
    except ExtractionError as exc:
        print(f"doc2md: {path.name}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - a bad file must not kill a batch
        print(f"doc2md: {path.name}: extraction failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    dest = _claim_dest(outdir, doc.slug, path, claimed)
    try:
        result = write_document(doc, dest, cfg, force=args.force)
    except FileExistsError as exc:
        print(f"doc2md: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"doc2md: {path.name}: could not write output: {exc}", file=sys.stderr)
        return EXIT_ERROR

    summary = f"{path.name} -> {result.markdown_path}"
    if result.images_written or result.placeholders:
        bits = []
        if result.images_written:
            bits.append(f"{result.images_written} image(s)")
        if result.placeholders:
            bits.append(f"{result.placeholders} placeholder(s)")
        summary += "  [" + ", ".join(bits) + "]"
    # Flush before the stderr notes so the two streams stay in order when
    # stdout is redirected.
    print(summary, flush=True)

    for note in result.notes:
        print(f"  note: {note}", file=sys.stderr)

    if args.check:
        problems = check_anchors(result.markdown_path)
        if problems:
            print(f"doc2md: --check failed for {result.markdown_path}:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_CHECK_FAILED
        if args.verbose:
            print("  check: anchors consistent")

    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc2md",
        description="Convert documents to Markdown with positionally anchored images. "
        "No network access and no API key required.",
    )
    parser.add_argument("files", nargs="*", type=Path, help="files to convert; omit for a picker")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help=f"where to write output (default: ./{OUTPUT_DIRNAME})",
    )
    parser.add_argument("--config", type=Path, default=None, help=f"path to {config_mod.CONFIG_NAME}")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output directory")
    parser.add_argument("--check", action="store_true", help="verify image anchors after converting")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)

    if cfg.unknown_keys and cfg.source_path:
        keys = ", ".join(cfg.unknown_keys)
        print(f"doc2md: ignoring unknown key(s) in {cfg.source_path}: {keys}", file=sys.stderr)

    files = list(args.files)
    if not files:
        files = pick_files(Path.cwd())
        if not files:
            return EXIT_OK

    outdir = (args.outdir or Path.cwd() / OUTPUT_DIRNAME).resolve()
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"doc2md: cannot create {outdir}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.config is None:
        written = config_mod.write_default()
        if written:
            print(f"doc2md: wrote default config to {written}")

    if args.verbose and cfg.source_path:
        print(f"doc2md: using config {cfg.source_path}")

    worst = EXIT_OK
    claimed: set[Path] = set()
    for path in files:
        code = convert_one(path, outdir, cfg, args, claimed)
        worst = code if code != EXIT_OK and worst == EXIT_OK else worst
    return worst


if __name__ == "__main__":
    sys.exit(main())
