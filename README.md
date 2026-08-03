# doc2md + docsum

Convert documents to Markdown with images anchored to the exact spot they
occupied in the original — then summarize long ones chapter by chapter, where
each chapter is summarized from its own text alone.

Two independent command-line tools that compose through the filesystem.

| Tool | Does | Needs |
|---|---|---|
| **`doc2md`** | Converts documents to Markdown, extracting images to sidecar files whose names mark exactly where the image sat in the original. | Nothing external. No network, no API key. |
| **`docsum`** | Summarizes a Markdown file. Long structured documents are summarized chapter by chapter. | An OpenAI API key. |

```
$ doc2md report.pdf                  ->  outputs/report/report.md
                                         outputs/report/images/
$ docsum outputs/report/report.md    ->  outputs/report/report.summary.md
```

They share no code and no dependencies. `doc2md` never imports `openai`;
`docsum` never imports any document library. Install either one alone.

Requires Python 3.10+.

## Why

Most document-to-Markdown converters either drop images entirely or dump them
in a pile with names like `image7.png`, losing the one thing that made them
worth extracting: *where they were*. `doc2md` gives every image an ID built
from its position — `annual-report-p012-img01` — and puts that ID inline in the
Markdown at the point the image occupied.

Most summarizers feed a whole document into one request and hope the model
holds it all. `docsum` splits on real section structure and sends **one chapter
per request**, so a 100,000-word book is summarized in ~6,000-word pieces
rather than one 100,000-word gulp.

## Install

```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
python3 -m venv .venv
.venv/bin/pip install -r requirements-doc2md.txt   # converter
.venv/bin/pip install -r requirements-docsum.txt   # summarizer
```

Run them from `bin/`, which picks up `.venv` automatically:

```bash
export PATH="$PWD/bin:$PATH"
doc2md --help
```

Or install properly, picking the tool you want:

```bash
pip install -e '.[doc2md]'      # just the converter
pip install -e '.[docsum]'      # just the summarizer
pip install -e '.[doc2md,docsum]'
```

`docsum` needs credentials at runtime:

```bash
export OPENAI_API_KEY='sk-...'
docsum --list-models        # confirm what this key can use
```

## Usage

Both tools take filenames as arguments, and both show a numbered picker when
run with none.

```bash
doc2md                        # pick from convertible files here
doc2md report.pdf notes.docx  # convert several at once
doc2md book.epub --check      # verify every image anchor resolves

docsum                        # pick from Markdown files under ./outputs
docsum outputs/report/report.md
docsum outputs/report/report.md --model gpt-5.4-mini
docsum --list-models          # what this API key can actually use
```

The picker accepts a number, a list (`1,3,5`), a range (`2-6`), or `all`.
Press Enter to cancel.

Both tools write into `./outputs`, so converted documents never mix with the
sources they came from:

```
outputs/
  report/
    report.md
    report.summary.md
    images/
      report-p012-img01.png
```

`doc2md` writes `outputs/<slug>/<slug>.md` plus `outputs/<slug>/images/`.
`docsum` writes `<name>.summary.md` beside its input when that input is already
under `outputs`, and into `outputs/` otherwise. Both refuse to overwrite without
`--force`, and both accept `--outdir` to go somewhere else entirely.

## Image anchoring

Every image occurrence gets an ID built from the position it held in the
source, so the Markdown records where images belong even when the bytes are
not extracted:

| Format | Locator | Example ID |
|---|---|---|
| PDF | page | `annual-report-p012-img01` |
| PPTX / ODP | slide | `q3-deck-s03-img02` |
| EPUB | spine position | `moby-dick-ch07-img01` |
| DOCX / HTML / Markdown / ODT | preceding heading | `spec-h04-img01` |

Extracted images are ordinary Markdown links:

```markdown
![annual-report-p012-img01: Revenue by segment](images/annual-report-p012-img01.png)
```

Anything not extracted keeps its position as a placeholder carrying whatever
caption or alt text the source had:

```markdown
*[spec-h04-img01 — remote image not downloaded: architecture diagram]*
```

Either way the ID is greppable, and `doc2md --check` verifies the invariant in
both directions: no anchor without a file or an explicit placeholder, and no
image file without an anchor. It exits non-zero on a mismatch, so it works in
a script.

**Image extraction is deliberately cheap.** Bytes are taken when the format
hands them over directly. Otherwise you get a placeholder. There is no OCR, no
layout reconstruction, no rasterizing of vector art, and remote URLs are never
fetched. Images below `min_image_pixels` are skipped, as are images repeated
across most pages of a PDF — letterheads and watermarks.

## Formats

| Format | Handled by | Images anchored |
|---|---|---|
| `.pdf` | PyMuPDF | yes, by page |
| `.docx` | python-docx | yes |
| `.pptx` | python-pptx | yes, by slide |
| `.epub` | EbookLib | yes, by chapter |
| `.html` `.htm` `.xhtml` | BeautifulSoup | yes, local files only |
| `.odt` `.odp` `.ods` | direct zip/XML | yes |
| `.md` `.markdown` | passthrough | yes, local files copied |
| `.txt` `.text` `.log` `.csv` | direct | n/a |
| `.rtf` | built-in stripper | no — text only |
| `.xlsx` `.xls` `.msg` `.ipynb` `.json` `.xml` | MarkItDown fallback | no — text only |

The MarkItDown fallback is limited to formats it genuinely converts. Formats
it would silently pass through as raw markup are not routed to it.

## How summarization works

`docsum` picks one of three strategies:

- **single** — under `chapter_threshold_words` (default 20,000): one request.
- **chapters** — long and has heading structure: split at the shallowest
  heading level producing at least two sections, then **one request per
  chapter carrying only that chapter's text**.
- **chunks** — long but with no usable headings: split on paragraph
  boundaries into ~8,000-word parts, so an oversized request is never sent.

Chapters run sequentially, and each may receive a capped excerpt of the
previous chapter's summary for narrative continuity (`carry_context`). A final
pass writes the overview from the chapter summaries, and it is placed first in
the output. Summaries target 5–15% of source length.

Sections already shorter than their own target are kept verbatim rather than
sent to the model — summarizing a 114-word table of contents into 150 words
wastes a request and reads absurdly.

Heading detection is fence-aware, so a `#` comment inside a code block is not
a heading. EPUB chapter titles come from the book's TOC when the chapter markup
carries no heading of its own. When a section references an image anchor, the
model is asked to cite it, so summaries stay linked to the extracted figures.

### Worked example

A 108,000-word EPUB textbook, on `gpt-5.4` at `effort: high`:

| | |
|---|---|
| `doc2md` | 0.7s — 28 images, 178 tables, `--check` clean |
| `docsum` | ~8 min, 16 requests, 108,000 → ~12,000 words (11%) |

Cost scales with chapter count: one request per chapter plus one overview.
Run with `-v` for a running token tally.

## Configuration

Two flat files, each read only by its own tool. Resolution order is
`--config PATH`, then `./<name>.yaml`, then `~/.config/<name>.yaml`, then
built-in defaults. A commented default is written on first run.

`doc2md.yaml`:

```yaml
extract_images: true
min_image_pixels: 10000        # skip rules, bullets, logos
max_image_bytes: 10485760
download_remote_images: false
```

`docsum.yaml`:

```yaml
model: gpt-5.4                 # `docsum --list-models` shows your options
effort: high                   # low | medium | high | xhigh | max
max_tokens: 16000
chapter_threshold_words: 20000
target_ratio: 0.10
min_ratio: 0.05
max_ratio: 0.15
min_words: 150
carry_context: true
```

`--model` overrides the configured model for one run.

### Choosing a model

`docsum` talks to the Chat Completions API, which every current OpenAI model
generation supports, so switching `model` is normally a one-line change.

`effort` maps to `reasoning_effort` and only applies to reasoning models
(o-series, `gpt-5` and later); `xhigh` and `max` both map to `high`. For other
models it is ignored. Reasoning models also spend part of `max_tokens` on
internal reasoning, so raise it if you switch to one and see truncated output.

Parameter support varies by generation — some models reject `temperature`,
others want `max_tokens` where newer ones want `max_completion_tokens`. Rather
than carry a table that goes stale, the client sends the modern set and retries
once without whatever parameter a 400 names, so an unfamiliar model degrades
into a working request instead of an error.

## Project layout

```
doc2md/           converter: model, config, render, extract/ (one module per format)
docsum/           summarizer: structure, prompts, client, runner
bin/              wrappers that use .venv automatically
requirements-*.txt
pyproject.toml    two extras, two console scripts
```

## Limitations

- PDF reading order is a positional sort, correct for single-column layouts;
  complex multi-column pages can interleave.
- PDF headings are inferred from relative font size, with the PDF outline
  overriding levels where it has entries. Many PDFs have neither, in which case
  the document has no headings and `docsum` falls back to chunking.
- Scanned PDFs produce no text. There is no OCR.
- EPUB image locators use spine position, not the printed chapter number, so
  front matter offsets them (chapter 5's figure may be `ch14-img01`).
- RTF conversion recovers paragraph text only.
- The MarkItDown fallback does not anchor images.
- `docsum` has no way to know a bibliography or index is not worth summarizing;
  it will spend a request on each.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, or the picker was cancelled |
| 1 | conversion, summarization, or I/O error |
| 2 | `doc2md --check` found an anchor inconsistency |

## License

See `LICENSE`.
