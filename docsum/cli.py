"""docsum command line interface.

    docsum                        numbered picker over Markdown files
    docsum FILE.md [FILE.md...]   summarize each file

Input is Markdown only. Run doc2md first to convert other formats.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from . import structure
from .client import Client, SummarizeError
from .runner import footer, summarize

EXIT_OK = 0
EXIT_ERROR = 1

MARKDOWN_SUFFIXES = {".md", ".markdown"}
SUMMARY_SUFFIX = ".summary.md"
OUTPUT_DIRNAME = "outputs"
# doc2md writes to ./outputs/<slug>/<slug>.md, two levels below the cwd.
SCAN_DEPTH = 2


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} MB"


def is_markdown(path: Path) -> bool:
    return path.suffix.lower() in MARKDOWN_SUFFIXES


def _is_summary(path: Path) -> bool:
    return path.name.lower().endswith(SUMMARY_SUFFIX)


# --- picker ----------------------------------------------------------------


def find_markdown(directory: Path, depth: int = SCAN_DEPTH) -> list[Path]:
    """Markdown files here and one level down, excluding existing summaries."""
    found: list[Path] = []

    def scan(folder: Path, level: int) -> None:
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.is_file() and is_markdown(entry) and not _is_summary(entry):
                found.append(entry)
            elif entry.is_dir() and level < depth and not entry.name.startswith("."):
                scan(entry, level + 1)

    scan(directory, 0)
    return found


def parse_selection(raw: str, count: int) -> list[int]:
    """Parse `3`, `1,4,5`, `2-6`, or `all` into zero-based indices."""
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
    files = find_markdown(directory)
    if not files:
        print(f"No Markdown files in {directory} or its subdirectories.", file=sys.stderr)
        print("Run doc2md on a document first to produce one.", file=sys.stderr)
        return []

    width = len(str(len(files)))
    print(f"Markdown files under {directory}:\n")
    for i, path in enumerate(files, 1):
        try:
            rel = path.relative_to(directory)
        except ValueError:
            rel = path
        words = structure.word_count(_read(path))
        print(f"  {i:>{width}}. {rel}  ({words:,} words, {_human_size(path.stat().st_size)})")
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
        print(f"docsum: {exc}", file=sys.stderr)
        return []


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


# --- summarization ---------------------------------------------------------


def _default_outdir(path: Path) -> Path:
    """Where a summary goes when --outdir is not given.

    Beside the input when it already lives under an `outputs` directory —
    which is where doc2md puts its Markdown — so a summary stays with the
    document and images it came from. Anywhere else, into ./outputs.
    """
    if OUTPUT_DIRNAME in path.resolve().parts:
        return path.parent
    return Path.cwd() / OUTPUT_DIRNAME


def _title_from(markdown: str, path: Path) -> str:
    for heading in structure.find_headings(markdown):
        if heading.level == 1 and heading.title:
            return heading.title
    return path.stem.replace("-", " ").replace("_", " ").strip() or path.name


def summarize_one(path: Path, client: Client, cfg, args) -> int:
    if not path.is_file():
        print(f"docsum: not a file: {path}", file=sys.stderr)
        return EXIT_ERROR

    if not is_markdown(path):
        print(
            f"docsum: {path.name} is not Markdown — run 'doc2md {path.name}' first, "
            f"then summarize the .md it produces.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    markdown = _read(path)
    if not markdown.strip():
        print(f"docsum: {path.name} is empty", file=sys.stderr)
        return EXIT_ERROR

    outdir = args.outdir or _default_outdir(path)
    destination = Path(outdir) / f"{path.stem}{SUMMARY_SUFFIX}"
    if destination.exists() and not args.force:
        print(f"docsum: {destination} already exists (use --force to overwrite)", file=sys.stderr)
        return EXIT_ERROR

    title = _title_from(markdown, path)
    print(f"{path.name}", flush=True)

    try:
        result = summarize(markdown, title, cfg, client, verbose=args.verbose)
    except SummarizeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(result.markdown + footer(result, cfg, path.name), encoding="utf-8")
    except OSError as exc:
        print(f"docsum: could not write {destination}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    ratio = (result.summary_words / result.plan.total_words * 100) if result.plan.total_words else 0
    print(
        f"  -> {destination}  "
        f"({result.plan.total_words:,} words -> {result.summary_words:,}, {ratio:.0f}%)",
        flush=True,
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docsum",
        description="Summarize a Markdown document. Long documents with section "
        "structure are summarized chapter by chapter, each from its own text only.",
    )
    parser.add_argument("files", nargs="*", type=Path, help="Markdown files; omit for a picker")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help=f"where to write summaries (default: beside the input if it is "
        f"under ./{OUTPUT_DIRNAME}, else ./{OUTPUT_DIRNAME})",
    )
    parser.add_argument("--config", type=Path, default=None, help=f"path to {config_mod.CONFIG_NAME}")
    parser.add_argument("--model", default=None, help="override the configured model")
    parser.add_argument("--force", action="store_true", help="overwrite an existing summary")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="list the models this API key can use, then exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def list_models(client: Client, cfg) -> int:
    try:
        models = client.list_models()
    except SummarizeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR

    if not models:
        print("docsum: this key has access to no models", file=sys.stderr)
        return EXIT_ERROR

    # Chat-capable ids first; the rest (embeddings, audio, image) after.
    chat = [m for m in models if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))]
    other = [m for m in models if m not in set(chat)]

    print(f"Models available to this key ({len(models)} total):\n")
    for name in chat:
        marker = "  * " if name == cfg.model else "    "
        print(f"{marker}{name}")
    if other:
        print(f"\n  ...plus {len(other)} non-chat models (embeddings, audio, image).")
    if cfg.model not in models:
        print(
            f"\nNote: the configured model {cfg.model!r} is NOT in this list. "
            f"Set 'model:' in docsum.yaml to one of the above.",
            file=sys.stderr,
        )
    else:
        print(f"\n  * = currently configured in docsum.yaml")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    if args.model:
        cfg.model = args.model

    if cfg.unknown_keys and cfg.source_path:
        keys = ", ".join(cfg.unknown_keys)
        print(f"docsum: ignoring unknown key(s) in {cfg.source_path}: {keys}", file=sys.stderr)

    if args.list_models:
        return list_models(Client(cfg=cfg, verbose=args.verbose), cfg)

    files = list(args.files)
    if not files:
        files = pick_files(Path.cwd())
        if not files:
            return EXIT_OK

    if args.config is None:
        written = config_mod.write_default()
        if written:
            print(f"docsum: wrote default config to {written}", flush=True)

    if args.verbose:
        source = cfg.source_path or "built-in defaults"
        print(f"docsum: model={cfg.model} effort={cfg.effort} config={source}", file=sys.stderr)

    client = Client(cfg=cfg, verbose=args.verbose)

    worst = EXIT_OK
    for path in files:
        code = summarize_one(path, client, cfg, args)
        if code != EXIT_OK and worst == EXIT_OK:
            worst = code

    if client.usage.calls:
        print(f"docsum: {client.usage}", file=sys.stderr)
    return worst


if __name__ == "__main__":
    sys.exit(main())
