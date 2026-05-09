# lingq-export

Pull your LingQs (vocabulary) from the [LingQ](https://www.lingq.com) API and
import them into [Anki](https://apps.ankiweb.net/) (via
[AnkiConnect](https://ankiweb.net/shared/info/2055492159)) or
[Mnemosyne](https://mnemosyne-proj.org/) as flashcards.  Or just dump them as
a TSV you can feed into a spreadsheet, manual import, or whatever else.

> **Attribution.**  This tool was written entirely by [Claude](https://claude.ai/)
> (Anthropic), at my request.  I (the repo owner) directed the design but did
> not write any of the code.  Bug reports and PRs welcome regardless.

## Why

LingQ only lets you export your vocabulary as one giant CSV with ~20 columns of
metadata, which is annoying if you just want "the 50 words I added today, in a
form my SRS understands".  The LingQ REST API gives you the same data as JSON
with proper sorting and filtering, and this script is a thin wrapper around it
that pushes the result into Anki or Mnemosyne (or writes a generic TSV).

## Install

No dependencies beyond Python 3.10+ standard library.

```bash
git clone https://github.com/CharlesR-W/lingq-export.git
cd lingq-export
chmod +x lingq-export.py
```

Or just download the single file and run it with `python3`.

### Anki side: install AnkiConnect

The Anki target talks to [AnkiConnect](https://ankiweb.net/shared/info/2055492159),
the standard third-party HTTP API addon for Anki.

1. In Anki: `Tools > Add-ons > Get Add-ons...` and paste code `2055492159`.
2. Restart Anki.  AnkiConnect listens on `http://localhost:8765` while Anki is
   running.

The Mnemosyne target talks directly to `~/Mnemosyne/default.db` (no addons
needed) and is unchanged from earlier versions of this script.

## Token setup

Get an API token from <https://www.lingq.com/accounts/apikey/>.

Either set an environment variable:

```bash
export LINGQ_TOKEN=your_token_here
```

...or write it to a file (recommended, so it persists across shells):

```bash
mkdir -p ~/.config/lingq
echo 'your_token_here' > ~/.config/lingq/token
chmod 600 ~/.config/lingq/token
```

## Usage

### Preview

```bash
python3 lingq-export.py --lang ru --n 10 --dry-run
```

Prints the front/back/tags it *would* produce, without touching anything.

### Import into Anki (default target)

Make sure Anki is running with AnkiConnect installed, then:

```bash
python3 lingq-export.py --lang ru --n 50
```

Cards land in deck `LingQ Import` (created if missing) using the `Basic` note
type, tagged `Russian` and `lingq-import`.  Override with `--deck`, `--notetype`,
`--front-field`, `--back-field` if you have a custom setup.

```bash
python3 lingq-export.py --lang ru --n 50 \
    --deck "Russian::Vocab" \
    --notetype "Basic (and reversed card)"
```

AnkiConnect's `addNotes` skips duplicates by default (scoped to the deck), so
re-runs won't double up.

### Import into Mnemosyne

```bash
python3 lingq-export.py --lang ru --n 50 --target mnemosyne
```

Writes directly to `~/Mnemosyne/default.db`.  Cards are created with new-card
sentinels (`grade=-1`, `next_rep=-1`) so they enter the normal new-card queue.
Tagged with the language name (e.g. "Russian"), `lingq-import`, and any tags
the LingQ itself carries.

Re-running with the same `--n` is safe: cards are deduped by exact front text.
If Mnemosyne is open the DB is locked, so the script queues the LingQs to
`~/.local/share/lingq/queue/` instead of failing — flush them later with
`--flush-queue`.

### Export to TSV (Anki File>Import, spreadsheets, manual review)

```bash
python3 lingq-export.py --lang ru --n 500 --tsv ~/Downloads/lingq-ru.tsv
```

Writes a 4-column TSV (`front<TAB>back<TAB>tags<TAB>deck`).  Anki's File >
Import dialog handles 4 columns natively (set the column mapping to Front,
Back, Tags, Deck).  Mnemosyne's TSV import only consumes the first two columns
and ignores the rest, which is fine.

Pass `--html` for `<b>`/`<i>`/`<br>` formatting if your target renders HTML.

### Filter by status

LingQ statuses range 0 (just added) to 4 (known).  Skip the ones you've
already mastered:

```bash
python3 lingq-export.py --lang ru --n 200 --status 0 1 2 3
```

### Card style

Three layouts (`--style`):

| Style       | Front                          | Back                       |
|-------------|--------------------------------|----------------------------|
| `word`      | term                           | hint                       |
| `context`   | term + example sentence (default) | hint                    |
| `cloze`     | sentence with term blanked     | term + hint                |

```bash
python3 lingq-export.py --lang ru --n 50 --style cloze
```

`cloze` is the sentence-mining style: front shows e.g. `Я ___ поехать в
отпуск`, back reveals `хочу / want`.  Falls back to `word` style if the
LingQ has no usable fragment.  Note that this is rendered as a regular
two-sided note, **not** as Anki's native cloze note type — if you want
true cloze deletions, set up a different note type with `{{c1::...}}` and
adapt the rendering.

### Reverse direction (production cards)

Use `--reverse` to flip front and back, so you're recalling the target-language
word *from* the English hint.  Pairs well with `--style word` for raw
production drills:

```bash
python3 lingq-export.py --lang ru --n 50 --style word --reverse
```

If you want both directions, run twice (once with `--reverse`, once without).
Cards dedupe by front, so they won't collide.

### Insertion order

LingQs are inserted in **oldest-first** order, so when you review the new-card
queue you start with the words you added first rather than yesterday's batch.
The LingQ API returns newest-first; the script reverses that for you.

### HTML formatting

By default the script emits plain text — which renders consistently across
SRS themes, spreadsheets, and human review.  If you want bolded terms and
italic example sentences, opt in with `--html`:

```bash
python3 lingq-export.py --lang ru --n 50 --html
```

This enables `<b>term</b>`, `<i>example sentence</i>`, and `<br>` linebreaks
throughout.

### Seen-pks dedup (deletions stick)

The script keeps a sidecar at `~/.local/share/lingq/seen/<lang>.json` listing
LingQ pks it has previously fetched.  Newly-fetched pks already in this set
are skipped, so a card you delete in your SRS won't be re-imported on the next
run.

If you want to re-add a deleted card, pass `--reimport` to ignore the sidecar.
If you're starting fresh and want to *only* import LingQs added from now on,
bootstrap with `--mark-seen` to seed the sidecar without importing anything.

## Card format

**Front** (default `--style context`, plain text):

```
хочу
Я хочу поехать в отпуск
```

The term on one line, the example sentence (LingQ's `fragment`) below.  If
the fragment is identical to the term, only the term is shown.

**Back:**

```
want
```

Or, with multiple hints joined: `want; wish; would like`.  User notes (if any)
are appended below.

Use `--reverse` to put the hint on the front instead (production practice:
see English, recall the target-language term).

## The Cloudflare gotcha

LingQ sits behind Cloudflare, which 403s any request with the default
`Python-urllib/3.x` User-Agent (error 1010, "browser_signature_banned").  This
is *not* documented anywhere obvious and the error message blames *you*, not
the UA.  The script sends a custom UA to dodge the block.

If you build your own LingQ client and hit a 1010, that's why.

## Caveats

- **Anki target requires AnkiConnect** and Anki running.  If Anki isn't open
  you'll get a connection error; close-and-reopen Anki, or use the TSV path
  for offline workflows.
- Anki tags are whitespace-delimited, so any internal spaces in LingQ tags
  are converted to `_` before import.
- **Mnemosyne target** is tested against the 2.10 schema.  If your DB is much
  older or much newer, the `cards`/`facts`/`tags` table layout may differ.
- Mnemosyne dedupe is by exact front text.  If you change the front-rendering
  logic and re-run, you'll get duplicates.  (Fix: clear the `lingq-import` tag
  in Mnemosyne, then re-run.)  Anki dedupe is also front-based via
  AnkiConnect's built-in `allowDuplicate: false`.
- LingQ's API rate limits are generous but not infinite.  Don't hammer with
  `--n 100000`.

## License

MIT.  See `LICENSE`.
