# lingq-to-mnemosyne

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
git clone https://github.com/CharlesR-W/lingq-to-mnemosyne.git
cd lingq-to-mnemosyne
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

Writes a 2-column TSV (`front<TAB>back`) with HTML formatting preserved.
LingQ's own tags (e.g. grammatical role) are appended to the back in italic.

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

### Mnemosyne is open?

The script will detect a locked DB and queue the LingQs to
`~/.local/share/lingq/queue/` instead of failing.  Once you close Mnemosyne:

```bash
python3 lingq-to-mnemosyne.py --flush-queue
```

## Card format

**Front:**

```html
<b>хочу</b><br><i>Я хочу поехать в отпуск</i>
```

The bold word and the example sentence (LingQ's `fragment`) it came from.  If
the fragment is identical to the term, only the term is shown.

**Back:**

```html
want
```

Or, with multiple hints joined: `want; wish; would like`.  User notes (if any)
are appended in italic.

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
