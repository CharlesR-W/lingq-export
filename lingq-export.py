#!/usr/bin/env python3
"""Fetch LingQs (vocabulary) from the LingQ API and import into an SRS.

Targets:
    --target anki       AnkiConnect on localhost:8765 (default)
    --target mnemosyne  Direct write to ~/Mnemosyne/default.db
    --tsv FILE          Write a TSV instead (works for either, plus spreadsheets)

Usage:
    python3 lingq-export.py --lang el --n 50
    python3 lingq-export.py --lang ru --n 100 --target mnemosyne
    python3 lingq-export.py --lang la --n 100 --dry-run
    python3 lingq-export.py --lang zh --n 50 --status 0 1 2

Token:
    Set $LINGQ_TOKEN, or write the token to ~/.config/lingq/token (chmod 600).
    Get yours from https://www.lingq.com/accounts/apikey/

Cards created:
    Front: <b>term</b>  (+ fragment context as smaller line if present)
    Back:  hint(s); user notes if any
    Tags:  <Language>, lingq-import, plus any LingQ tags on the word (opt-in)

Dedupes by LingQ pk via a sidecar seen-pks file, so re-running with the same
--n is safe and SRS-side deletions stick across re-imports.
"""

import argparse
import json
import os
import random
import re
import sqlite3
import string
import subprocess
import sys
import time
import urllib.parse
import urllib.request


MNEMOSYNE_DB = os.path.expanduser("~/Mnemosyne/default.db")
QUEUE_DIR = os.path.expanduser("~/.local/share/lingq/queue/")
SEEN_DIR = os.path.expanduser("~/.local/share/lingq/seen/")
LOG_PATH = os.path.expanduser("~/.local/share/lingq/import.log")
TOKEN_FILE = os.path.expanduser("~/.config/lingq/token")

# Mnemosyne schema constants (tested against 2.10).
CARD_TYPE_ID = "1"
FACT_VIEW_ID = "1.1"

# Anki defaults.
DEFAULT_ANKI_URL = "http://localhost:8765"
DEFAULT_DECK = "LingQ Import"
DEFAULT_NOTETYPE = "Basic"
DEFAULT_FRONT_FIELD = "Front"
DEFAULT_BACK_FIELD = "Back"

# Friendly tag names; fall back to the API code if unknown.
LANG_NAMES = {
    "el": "Greek",
    "la": "Latin",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "pt": "Portuguese",
    "nl": "Dutch",
}


# ---------------------------------------------------------------------------
# Token loading
# ---------------------------------------------------------------------------

def _seen_path(lang):
    return os.path.join(SEEN_DIR, f"{lang}.json")


def load_seen(lang):
    """Set of LingQ pks we've previously fetched for this language.

    Used to skip cards the user has deleted from their SRS: front-text dedup
    can't see them once they're gone, but the pk lives on in this sidecar
    forever.  Delete a card -> pk stays seen -> never re-imports.
    """
    path = _seen_path(lang)
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return set(json.load(f))


def save_seen(lang, pks):
    os.makedirs(SEEN_DIR, exist_ok=True)
    with open(_seen_path(lang), "w") as f:
        json.dump(sorted(int(p) for p in pks if p is not None), f)


def load_token():
    tok = os.environ.get("LINGQ_TOKEN", "").strip()
    if tok:
        return tok
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    sys.exit(
        "No token found. Set $LINGQ_TOKEN, or write it to "
        f"{TOKEN_FILE} (chmod 600).\n"
        "Get a token at https://www.lingq.com/accounts/apikey/"
    )


# ---------------------------------------------------------------------------
# LingQ API
# ---------------------------------------------------------------------------

def fetch_lingqs(token, lang, n, status_filter=None):
    """Fetch the N newest LingQs for a language.

    Default ordering on the API is newest-first by pk, so we just walk pages
    until we have N (or run out).
    """
    base = f"https://www.lingq.com/api/v3/{lang}/cards/"
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        # Cloudflare on lingq.com 403s the default Python-urllib UA.
        "User-Agent": "lingq-export/1.0 (+https://github.com/CharlesR-W/lingq-export)",
    }
    out = []
    page_size = min(n, 200)
    page = 1
    while len(out) < n:
        params = {"page": page, "page_size": page_size, "sort": "date"}
        if status_filter:
            qs = urllib.parse.urlencode(params)
            qs += "".join(f"&status={s}" for s in status_filter)
        else:
            qs = urllib.parse.urlencode(params)

        url = f"{base}?{qs}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            sys.exit(f"LingQ API error {e.code}: {e.read().decode('utf-8', 'ignore')}")
        except urllib.error.URLError as e:
            sys.exit(f"Network error reaching LingQ: {e}")

        results = data.get("results", [])
        if not results:
            break
        out.extend(results)
        if not data.get("next"):
            break
        page += 1

    return out[:n]


# ---------------------------------------------------------------------------
# Front/back rendering
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s):
    """Render HTML formatting as plain text. Newlines become ' | '."""
    s = s.replace("<br>", " | ").replace("<br/>", " | ").replace("<br />", " | ")
    return _HTML_TAG_RE.sub("", s).strip()


def render_card(lq, style="context", reverse=False, html=False):
    """Project a LingQ API record down to (front, back, tags).

    style:
        "word"    - front = term, back = hint(s)
        "context" - front = term, back = hint(s) + example sentence  [default]
        "cloze"   - front = sentence with term blanked, back = term + hint(s)
                    (rendered as a Basic note, NOT Anki's cloze note type)
    reverse: swap front and back (production-direction cards).
    html: emit <b>/<i>/<br> formatting. Default is plain text, which renders
          consistently across SRS themes and other frontends.
    """
    term = (lq.get("term") or "").strip()
    fragment = (lq.get("fragment") or "").strip()
    notes = (lq.get("notes") or "").strip()
    hints = lq.get("hints") or []
    tags = lq.get("tags") or []

    if html:
        bold = lambda s: f"<b>{s}</b>"
        ital = lambda s: f"<i>{s}</i>"
        br = "<br>"
        blank = "<b>___</b>"
    else:
        bold = lambda s: s
        ital = lambda s: s
        br = "\n"
        blank = "___"

    hint_texts = [h.get("text", "").strip() for h in hints if h.get("text")]
    hints_str = "; ".join(hint_texts) if hint_texts else ital("(no hint)")

    if style == "word":
        front = bold(term)
        back = hints_str
    elif style == "cloze":
        if fragment and term and term in fragment:
            front = fragment.replace(term, blank, 1)
        else:
            front = bold(term)
        back = f"{bold(term)}{br}{hints_str}"
    else:  # "context" (default)
        front = bold(term)
        back = hints_str
        if fragment and fragment.lower() != term.lower():
            back += f"{br}{ital(fragment)}"

    if notes:
        back += f"{br}{ital(notes)}"

    if reverse:
        front, back = back, front

    return front, back, tags


# ---------------------------------------------------------------------------
# Anki backend (AnkiConnect)
# ---------------------------------------------------------------------------

def _anki_tag(t):
    # Anki tags are whitespace-delimited, so internal spaces become '_'.
    return t.strip().replace(" ", "_")


def _anki_request(url, action, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach AnkiConnect at {url}: {e}.  "
            "Make sure Anki is running and the AnkiConnect addon is installed "
            "(https://ankiweb.net/shared/info/2055492159)."
        )
    if data.get("error"):
        raise RuntimeError(f"AnkiConnect error: {data['error']}")
    return data.get("result")


def import_to_anki(lingqs, lang_code, deck=DEFAULT_DECK, notetype=DEFAULT_NOTETYPE,
                   front_field=DEFAULT_FRONT_FIELD, back_field=DEFAULT_BACK_FIELD,
                   anki_url=DEFAULT_ANKI_URL,
                   style="context", reverse=False, html=False, lingq_tags=False):
    # Verify AnkiConnect is reachable before queueing work.
    try:
        _anki_request(anki_url, "version")
    except RuntimeError as e:
        sys.exit(str(e))

    # Verify the requested note type exists, with the requested fields.
    try:
        models = _anki_request(anki_url, "modelNames")
        if notetype not in models:
            sys.exit(
                f"Note type {notetype!r} not found in Anki.  "
                f"Available: {', '.join(sorted(models))}.  "
                "Pick one with --notetype, or create a Basic note type in Anki."
            )
        fields = _anki_request(anki_url, "modelFieldNames", modelName=notetype)
        for f in (front_field, back_field):
            if f not in fields:
                sys.exit(
                    f"Field {f!r} not found in note type {notetype!r}.  "
                    f"Available fields: {', '.join(fields)}.  "
                    "Use --front-field / --back-field to map."
                )
    except RuntimeError as e:
        sys.exit(str(e))

    # Make sure the deck exists.
    _anki_request(anki_url, "createDeck", deck=deck)

    lang_tag = LANG_NAMES.get(lang_code, lang_code)
    base_tags = [lang_tag, "lingq-import"]

    notes = []
    # Oldest-first so the new-card queue starts with the words you added first.
    for lq in reversed(lingqs):
        front, back, extra_tags = render_card(
            lq, style=style, reverse=reverse, html=html
        )
        if not front:
            continue
        tags = list(base_tags)
        if lingq_tags:
            tags.extend(_anki_tag(t) for t in extra_tags if t)
        notes.append({
            "deckName": deck,
            "modelName": notetype,
            "fields": {front_field: front, back_field: back},
            "tags": tags,
            "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        })

    if not notes:
        print("Nothing to import.")
        return

    try:
        results = _anki_request(anki_url, "addNotes", notes=notes)
    except RuntimeError as e:
        sys.exit(str(e))

    added = sum(1 for r in results if r is not None)
    skipped = len(results) - added
    print(f"Anki: {added} note(s) added, {skipped} duplicate(s) skipped (deck: {deck!r})")
    log(f"anki imported lang={lang_code} added={added} skipped={skipped} "
        f"deck={deck} notetype={notetype} style={style} reverse={reverse} html={html}")


# ---------------------------------------------------------------------------
# Mnemosyne backend (direct SQLite)
# ---------------------------------------------------------------------------

def random_id(length=22):
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def get_or_create_tag(conn, tag_text):
    row = conn.execute("SELECT _id FROM tags WHERE name = ?", (tag_text,)).fetchone()
    if row:
        return row[0]
    conn.execute(
        "INSERT INTO tags (name, id, extra_data) VALUES (?, ?, '')",
        (tag_text, random_id()),
    )
    conn.commit()
    return conn.execute(
        "SELECT _id FROM tags WHERE name = ?", (tag_text,)
    ).fetchone()[0]


def card_exists(conn, front_text):
    row = conn.execute(
        "SELECT 1 FROM data_for_fact d "
        "JOIN facts f ON f._id = d._fact_id "
        "WHERE d.key = 'f' AND d.value = ? LIMIT 1",
        (front_text,),
    ).fetchone()
    return row is not None


def insert_card(conn, front, back, tag_ids, tags_str):
    now = int(time.time())
    fact_id = random_id()
    conn.execute("INSERT INTO facts (id, extra_data) VALUES (?, '')", (fact_id,))
    fact_internal_id = conn.execute(
        "SELECT _id FROM facts WHERE id = ?", (fact_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO data_for_fact (_fact_id, key, value) VALUES (?, 'f', ?)",
        (fact_internal_id, front),
    )
    conn.execute(
        "INSERT INTO data_for_fact (_fact_id, key, value) VALUES (?, 'b', ?)",
        (fact_internal_id, back),
    )

    card_id = random_id()
    # New-card sentinels: grade=-1 (unseen), next_rep=-1 (unscheduled), last_rep=-1.
    # grade=0 makes Mnemosyne think the card was rated "blackout" and throws
    # "internal error: interval not zero" on first grading.
    conn.execute(
        """
        INSERT INTO cards (
            id, card_type_id, _fact_id, fact_view_id,
            question, answer, tags,
            grade, next_rep, last_rep,
            easiness, acq_reps, ret_reps, lapses,
            acq_reps_since_lapse, ret_reps_since_lapse,
            creation_time, modification_time,
            active, extra_data, scheduler_data
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?,
            -1, -1, -1,
            2.5, 0, 0, 0,
            0, 0,
            ?, ?,
            1, '', 0
        )
        """,
        (card_id, CARD_TYPE_ID, fact_internal_id, FACT_VIEW_ID,
         front, back, tags_str, now, now),
    )
    card_internal_id = conn.execute(
        "SELECT _id FROM cards WHERE id = ?", (card_id,)
    ).fetchone()[0]
    for tid in tag_ids:
        conn.execute(
            "INSERT INTO tags_for_card (_card_id, _tag_id) VALUES (?, ?)",
            (card_internal_id, tid),
        )


def import_to_mnemosyne(lingqs, lang_code, db_path=MNEMOSYNE_DB,
                        style="context", reverse=False, html=False, lingq_tags=False):
    if not os.path.exists(db_path):
        sys.exit(f"Mnemosyne database not found: {db_path}")

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        os.makedirs(QUEUE_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(QUEUE_DIR, f"lingq-{lang_code}-{stamp}.json")
        with open(dest, "w") as f:
            json.dump(lingqs, f, ensure_ascii=False)
        log(f"QUEUED {dest} (Mnemosyne DB locked)")
        print(f"Mnemosyne is running (DB locked). Queued: {dest}")
        print("Run with --flush-queue after closing Mnemosyne.")
        return

    lang_tag = LANG_NAMES.get(lang_code, lang_code)
    base_tag_ids = [
        get_or_create_tag(conn, lang_tag),
        get_or_create_tag(conn, "lingq-import"),
    ]

    added = skipped = 0
    # Oldest-first so the new-card queue starts with the words you added first.
    for lq in reversed(lingqs):
        front, back, extra_tags = render_card(lq, style=style, reverse=reverse, html=html)
        if not front:
            continue
        if card_exists(conn, front):
            skipped += 1
            continue
        # LingQ's per-word tags (inflection form, subject pronoun, grammatical
        # category) are usually noise at Mnemosyne-scale.  Opt in with
        # --lingq-tags if you want them (e.g. for filtering by grammatical role).
        use_extra = extra_tags if lingq_tags else []
        tag_ids = list(base_tag_ids)
        for t in use_extra:
            tag_ids.append(get_or_create_tag(conn, t))
        tags_str = ", ".join(
            sorted({lang_tag, "lingq-import", *use_extra})
        )
        insert_card(conn, front, back, tag_ids, tags_str)
        added += 1

    conn.commit()
    conn.close()
    print(f"Mnemosyne: {added} card(s) added, {skipped} duplicate(s) skipped")
    log(f"mnemosyne imported lang={lang_code} added={added} skipped={skipped} "
        f"style={style} reverse={reverse} html={html}")


def flush_queue(db_path=MNEMOSYNE_DB):
    if not os.path.isdir(QUEUE_DIR):
        print("No queue directory.")
        return
    files = sorted(f for f in os.listdir(QUEUE_DIR) if f.endswith(".json"))
    if not files:
        print("Queue is empty.")
        return
    for f in files:
        path = os.path.join(QUEUE_DIR, f)
        try:
            lang_code = f.split("-")[1]
        except IndexError:
            lang_code = "unknown"
        with open(path) as fp:
            lingqs = json.load(fp)
        print(f"Importing queued: {f} ({len(lingqs)} entries)")
        import_to_mnemosyne(lingqs, lang_code, db_path=db_path)
        os.remove(path)
    print(f"Flushed {len(files)} queued file(s).")


# ---------------------------------------------------------------------------
# TSV (Anki File>Import, spreadsheets, manual review)
# ---------------------------------------------------------------------------

def _tsv_safe(s):
    # Newlines become <br> so they survive the TSV row and render as line
    # breaks in Anki's Basic note type (which interprets HTML).  A literal
    # space separator silently glues term and sentence together; a literal
    # \n would break the row.
    return s.replace("\t", " ").replace("\n", "<br>").replace("\r", "")


def write_tsv(lingqs, path, lang_code, deck=DEFAULT_DECK,
              style="context", reverse=False, html=False, lingq_tags=False):
    """Write a 4-column TSV (front, back, tags, deck).

    Anki's File > Import dialog takes 4 columns natively (configure as
    Front, Back, Tags, Deck).  Mnemosyne's TSV import is 2-column; the
    extra columns are harmless if you only map the first two.
    """
    lang_tag = LANG_NAMES.get(lang_code, lang_code)
    base_tags = [lang_tag, "lingq-import"]
    written = 0
    with open(path, "w", encoding="utf-8") as f:
        for lq in reversed(lingqs):
            front, back, extra_tags = render_card(
                lq, style=style, reverse=reverse, html=html
            )
            if not front:
                continue
            tags = list(base_tags)
            if lingq_tags:
                tags.extend(_anki_tag(t) for t in extra_tags if t)
            f.write(
                f"{_tsv_safe(front)}\t{_tsv_safe(back)}\t{' '.join(tags)}\t{deck}\n"
            )
            written += 1
    print(f"Wrote {written} cards -> {path}")
    log(f"tsv lang={lang_code} count={written} path={path} deck={deck} "
        f"style={style} reverse={reverse} html={html}")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {msg}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", help="LingQ language code (el, la, zh, ...)")
    ap.add_argument("--n", type=int, default=50, help="Number of newest LingQs to fetch (default 50)")
    ap.add_argument("--status", type=int, nargs="+", help="Only fetch LingQs with these status codes (0=new ... 4=known)")
    ap.add_argument("--target", choices=["anki", "mnemosyne"], default="anki",
                    help="SRS to import into. anki uses AnkiConnect on localhost:8765 (default). "
                         "mnemosyne writes directly to ~/Mnemosyne/default.db.")
    ap.add_argument("--style", choices=["word", "context", "cloze"], default="context",
                    help="Card layout: word (term->hint), context (term+sentence->hint, default), "
                         "cloze (sentence-with-blank->term+hint)")
    ap.add_argument("--reverse", action="store_true",
                    help="Swap front/back (production practice: hint->term)")
    ap.add_argument("--html", action="store_true",
                    help="Emit <b>/<i>/<br> formatting (default is plain text, which renders "
                         "consistently across SRS themes and other frontends)")
    ap.add_argument("--lingq-tags", action="store_true",
                    help="Import LingQ's per-word tags (inflection form, pronoun, grammatical "
                         "category). Default: skip - <Language>/lingq-import only.")

    # Anki-specific
    ap.add_argument("--deck", default=DEFAULT_DECK,
                    help=f"Deck name (Anki target only; default: {DEFAULT_DECK!r}). Created if missing.")
    ap.add_argument("--notetype", default=DEFAULT_NOTETYPE,
                    help=f"Anki note type / model (default: {DEFAULT_NOTETYPE!r})")
    ap.add_argument("--front-field", default=DEFAULT_FRONT_FIELD,
                    help=f"Field name to populate as front (default: {DEFAULT_FRONT_FIELD!r})")
    ap.add_argument("--back-field", default=DEFAULT_BACK_FIELD,
                    help=f"Field name to populate as back (default: {DEFAULT_BACK_FIELD!r})")
    ap.add_argument("--anki-url", default=DEFAULT_ANKI_URL,
                    help=f"AnkiConnect URL (default: {DEFAULT_ANKI_URL})")

    # Mnemosyne-specific
    ap.add_argument("--mnemosyne-db", default=MNEMOSYNE_DB,
                    help=f"Path to Mnemosyne SQLite DB (default: {MNEMOSYNE_DB})")

    ap.add_argument("--reimport", action="store_true",
                    help="Ignore the seen-pks sidecar and re-consider every fetched LingQ.")
    ap.add_argument("--mark-seen", action="store_true",
                    help="Fetch and record pks as 'seen' but do not import.")
    ap.add_argument("--dry-run", action="store_true", help="Print front/back to stdout, do not touch the SRS")
    ap.add_argument("--tsv", metavar="FILE", help="Write 4-col TSV (front\\tback\\ttags\\tdeck) to FILE; do not touch the SRS")
    ap.add_argument("--flush-queue", action="store_true",
                    help="Import any LingQs queued while Mnemosyne was open (Mnemosyne target only)")
    args = ap.parse_args()

    if args.flush_queue:
        flush_queue(db_path=args.mnemosyne_db)
        return

    if not args.lang:
        ap.error("--lang is required (e.g. --lang el)")

    token = load_token()
    lingqs = fetch_lingqs(token, args.lang, args.n, args.status)
    print(f"Fetched {len(lingqs)} LingQ(s) for {args.lang}")

    seen = set() if args.reimport else load_seen(args.lang)
    if seen and not args.reimport:
        before = len(lingqs)
        lingqs = [lq for lq in lingqs if lq.get("pk") not in seen]
        print(f"Filtered {before - len(lingqs)} previously-seen pks; {len(lingqs)} remain")

    if args.mark_seen:
        all_pks = {lq.get("pk") for lq in lingqs} | seen
        save_seen(args.lang, all_pks)
        print(f"Recorded {len(all_pks)} pks as seen for {args.lang} (no import performed)")
        return

    if args.dry_run:
        for lq in lingqs:
            front, back, tags = render_card(
                lq, style=args.style, reverse=args.reverse, html=args.html
            )
            print("---")
            print(f"FRONT: {front}")
            print(f"BACK : {back}")
            if tags:
                print(f"TAGS : {', '.join(tags)}")
        return

    if args.tsv:
        write_tsv(lingqs, args.tsv, args.lang, deck=args.deck,
                  style=args.style, reverse=args.reverse, html=args.html,
                  lingq_tags=args.lingq_tags)
        # TSV mode: no seen-pks update — caller controls what happens to the file.
        return

    if args.target == "anki":
        import_to_anki(
            lingqs, args.lang,
            deck=args.deck, notetype=args.notetype,
            front_field=args.front_field, back_field=args.back_field,
            anki_url=args.anki_url,
            style=args.style, reverse=args.reverse, html=args.html,
            lingq_tags=args.lingq_tags,
        )
    else:  # mnemosyne
        import_to_mnemosyne(
            lingqs, args.lang, db_path=args.mnemosyne_db,
            style=args.style, reverse=args.reverse, html=args.html,
            lingq_tags=args.lingq_tags,
        )

    processed_pks = {lq.get("pk") for lq in lingqs if lq.get("pk") is not None}
    save_seen(args.lang, seen | processed_pks)


if __name__ == "__main__":
    main()
