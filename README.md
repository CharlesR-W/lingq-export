# lingq-export

Pull your LingQs (vocabulary) from the [LingQ](https://www.lingq.com) API and
import them into [Mnemosyne](https://mnemosyne-proj.org/) as flashcards.  Or
just dump them as a TSV you can feed into Anki, a spreadsheet, or whatever
else.

> **Attribution.**  This tool was written entirely by [Claude](https://claude.ai/)
> (Anthropic), at my request, in a single session.  I (the repo owner) directed
> the design but did not write any of the code.  Bug reports and PRs welcome
> regardless.

## Why

LingQ only lets you export your vocabulary as one giant CSV with ~20 columns of
metadata, which is annoying if you just want "the 50 words I added today, in a
form my SRS understands".  The LingQ REST API gives you the same data as JSON
with proper sorting and filtering, and this script is a thin wrapper around it
that targets Mnemosyne's SQLite schema (or writes a generic TSV).

## Install

No dependencies beyond Python 3.10+.  The script uses only the standard
library.

```bash
git clone https://github.com/CharlesR-W/lingq-export.git
cd lingq-export
chmod +x lingq-to-mnemosyne.py
```

Or just download the single file and run it with `python3`.

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
python3 lingq-to-mnemosyne.py --lang ru --n 10 --dry-run
```

Prints the front/back/tags it *would* produce, without touching anything.

### Export to TSV (Anki, spreadsheets, manual review)

```bash
python3 lingq-to-mnemosyne.py --lang ru --n 500 --tsv ~/Downloads/lingq-ru.tsv
```

Writes a 2-column TSV (`front<TAB>back`) in plain text.  LingQ's own tags
(e.g. grammatical role) are appended to the back in `[brackets]`.  Pass
`--html` for `<b>`/`<i>`/`<br>` formatting if your target renders HTML.

### Import into Mnemosyne

```bash
python3 lingq-to-mnemosyne.py --lang ru --n 50
```

Writes directly to `~/Mnemosyne/default.db`.  Cards are created with `grade=0`
so they enter the normal new-card queue.  Tagged with the language name (e.g.
"Russian"), `lingq-import`, and any tags the LingQ itself carries.

Re-running with the same `--n` is safe: cards are deduped by exact front text.

### Filter by status

LingQ statuses range 0 (just added) to 4 (known).  Skip the ones you've
already mastered:

```bash
python3 lingq-to-mnemosyne.py --lang ru --n 200 --status 0 1 2 3
```

### Card style

Three layouts (`--style`):

| Style       | Front                          | Back                       |
|-------------|--------------------------------|----------------------------|
| `word`      | term                           | hint                       |
| `context`   | term + example sentence (default) | hint                    |
| `cloze`     | sentence with term blanked     | term + hint                |

```bash
python3 lingq-to-mnemosyne.py --lang ru --n 50 --style cloze
```

`cloze` is the sentence-mining style: front shows e.g. `Я ___ поехать в
отпуск`, back reveals `хочу / want`.  Falls back to `word` style if the
LingQ has no usable fragment.

### Reverse direction (production cards)

Use `--reverse` to flip front and back, so you're recalling the target-language
word *from* the English hint.  Pairs well with `--style word` for raw
production drills:

```bash
python3 lingq-to-mnemosyne.py --lang ru --n 50 --style word --reverse
```

If you want both directions, run twice (once with `--reverse`, once without).
Cards dedupe by exact front text, so they won't collide.

### HTML formatting

By default the script emits plain text — which renders consistently across
Mnemosyne themes, Anki, spreadsheets, and human review.  If you want bolded
terms and italic example sentences, opt in with `--html`:

```bash
python3 lingq-to-mnemosyne.py --lang ru --n 50 --html
```

This enables `<b>term</b>`, `<i>example sentence</i>`, and `<br>` linebreaks
throughout.

### Mnemosyne is open?

The script will detect a locked DB and queue the LingQs to
`~/.local/share/lingq/queue/` instead of failing.  Once you close Mnemosyne:

```bash
python3 lingq-to-mnemosyne.py --flush-queue
```

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

## The Cloudflare gotcha

LingQ sits behind Cloudflare, which 403s any request with the default
`Python-urllib/3.x` User-Agent (error 1010, "browser_signature_banned").  This
is *not* documented anywhere obvious and the error message blames *you*, not
the UA.  The script sends a custom UA to dodge the block.

If you build your own LingQ client and hit a 1010, that's why.

## Caveats

- Tested against Mnemosyne 2.10 schema.  If your DB is much older or much
  newer, the `cards`/`facts`/`tags` table layout may differ.
- Dedupe is by exact front text.  If you change the front-rendering logic and
  re-run, you'll get duplicates.  (Fix: clear the `lingq-import` tag in
  Mnemosyne, then re-run.)
- LingQ's API rate limits are generous but not infinite.  Don't hammer with
  `--n 100000`.

## License

MIT.  See `LICENSE`.
