#!/usr/bin/env python3
"""Fetch LingQs (vocabulary) from the LingQ API and import into Mnemosyne.

Usage:
    python3 lingq-to-mnemosyne.py --lang el --n 50
    python3 lingq-to-mnemosyne.py --lang la --n 100 --dry-run
    python3 lingq-to-mnemosyne.py --lang zh --n 50 --status 0 1 2

Token:
    Set $LINGQ_TOKEN, or write the token to ~/.config/lingq/token (chmod 600).
    Get yours from https://www.lingq.com/accounts/apikey/

Cards created:
    Front: <b>term</b>  (+ fragment context as smaller line if present)
    Back:  hint(s); user notes if any
    Tags:  <Language>, lingq-import, plus any LingQ tags on the word
    grade=0 (new/unseen) so they enter the normal new-card queue.

Dedupes by exact front text (so re-running with the same --n is safe).
"""

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import string
import sys
import time
import urllib.parse
import urllib.request


MNEMOSYNE_DB = os.path.expanduser("~/Mnemosyne/default.db")
QUEUE_DIR = os.path.expanduser("~/.local/share/lingq/queue/")
LOG_PATH = os.path.expanduser("~/.local/share/lingq/import.log")
TOKEN_FILE = os.path.expanduser("~/.config/lingq/token")
CARD_TYPE_ID = "1"
FACT_VIEW_ID = "1.1"

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
        "User-Agent": "lingq-to-mnemosyne/1.0 (+https://github.com/)",
    }
    out = []
    page_size = min(n, 200)
    page = 1
    while len(out) < n:
        params = {"page": page, "page_size": page_size, "sort": "date"}
        if status_filter:
            # API accepts repeated status= params; encode manually
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
        "context" - front = term + example sentence, back = hint(s)  [default]
        "cloze"   - front = sentence with term blanked, back = term + hint(s)
    reverse: swap front and back (production-direction cards).
    html: emit <b>/<i>/<br> formatting. Default is plain text, which renders
          consistently across Mnemosyne themes and other SRS frontends.
    """
    term = (lq.get("term") or "").strip()
    fragment = (lq.get("fragment") or "").strip()
    notes = (lq.get("notes") or "").strip()
    hints = lq.get("hints") or []
    tags = lq.get("tags") or []

    # Formatting helpers switched on the html flag.
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
            # No usable fragment - degrade gracefully to term-only front.
            front = bold(term)
        back = f"{bold(term)}{br}{hints_str}"
    else:  # "context" (default)
        front = bold(term)
        if fragment and fragment.lower() != term.lower():
            front += f"{br}{ital(fragment)}"
        back = hints_str

    if notes:
        back += f"{br}{ital(notes)}"

    # Default is production direction: hint on front, term on back.  --reverse
    # flips to recognition direction (term on front, hint on back) - useful
    # for reading practice.
    if not reverse:
        front, back = back, front

    return front, back, tags


# ---------------------------------------------------------------------------
# Mnemosyne import (mirrors lute-to-mnemosyne.py)
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
            0, 0, -1,
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


def import_to_mnemosyne(lingqs, lang_code, style="context", reverse=False, html=False):
    if not os.path.exists(MNEMOSYNE_DB):
        sys.exit(f"Mnemosyne database not found: {MNEMOSYNE_DB}")

    try:
        conn = sqlite3.connect(MNEMOSYNE_DB, timeout=10)
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
    for lq in lingqs:
        front, back, extra_tags = render_card(lq, style=style, reverse=reverse, html=html)
        if not front:
            continue
        if card_exists(conn, front):
            skipped += 1
            continue
        tag_ids = list(base_tag_ids)
        for t in extra_tags:
            tag_ids.append(get_or_create_tag(conn, t))
        tags_str = ", ".join(
            sorted({lang_tag, "lingq-import", *extra_tags})
        )
        insert_card(conn, front, back, tag_ids, tags_str)
        added += 1

    conn.commit()
    conn.close()
    print(f"Mnemosyne import: {added} cards added, {skipped} duplicates skipped")
    log(f"imported lang={lang_code} added={added} skipped={skipped} style={style} reverse={reverse} html={html}")


def write_tsv(lingqs, path, lang_code, style="context", reverse=False, html=False):
    """Write TSV: front<TAB>back, one card per line.

    Default is plain text; pass html=True for <b>/<i>/<br> formatting.
    LingQ tags are appended to the back since stock TSV import handles 2
    columns reliably.
    """
    written = 0
    with open(path, "w", encoding="utf-8") as f:
        for lq in lingqs:
            front, back, tags = render_card(
                lq, style=style, reverse=reverse, html=html
            )
            if not front:
                continue
            if tags:
                tag_str = ", ".join(tags)
                back += f"<br><i>[{tag_str}]</i>" if html else f" | [{tag_str}]"
            # Strip anything that would corrupt the TSV row
            front = front.replace("\t", " ").replace("\n", " ").replace("\r", "")
            back = back.replace("\t", " ").replace("\n", " ").replace("\r", "")
            f.write(f"{front}\t{back}\n")
            written += 1
    print(f"Wrote {written} cards -> {path}")
    log(f"tsv lang={lang_code} count={written} path={path} style={style} reverse={reverse} html={html}")


def flush_queue():
    if not os.path.isdir(QUEUE_DIR):
        print("No queue directory.")
        return
    files = sorted(f for f in os.listdir(QUEUE_DIR) if f.endswith(".json"))
    if not files:
        print("Queue is empty.")
        return
    for f in files:
        path = os.path.join(QUEUE_DIR, f)
        # Filename pattern: lingq-<lang>-<stamp>.json
        try:
            lang_code = f.split("-")[1]
        except IndexError:
            lang_code = "unknown"
        with open(path) as fp:
            lingqs = json.load(fp)
        print(f"Importing queued: {f} ({len(lingqs)} entries)")
        import_to_mnemosyne(lingqs, lang_code)
        os.remove(path)
    print(f"Flushed {len(files)} queued file(s).")


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
    ap.add_argument("--style", choices=["word", "context", "cloze"], default="context",
                    help="Card layout: word (term->hint), context (term+sentence->hint, default), cloze (sentence-with-blank->term+hint)")
    ap.add_argument("--reverse", action="store_true",
                    help="Recognition direction: term on front, hint on back (reading practice). Default is production: hint on front, term on back (recall/speaking practice).")
    ap.add_argument("--html", action="store_true",
                    help="Emit <b>/<i>/<br> formatting (default is plain text, which renders consistently across Mnemosyne themes and other SRS frontends)")
    ap.add_argument("--dry-run", action="store_true", help="Print front/back to stdout, do not touch Mnemosyne")
    ap.add_argument("--tsv", metavar="FILE", help="Write TSV (front\\tback) to FILE; do not touch Mnemosyne DB")
    ap.add_argument("--flush-queue", action="store_true", help="Import any LingQs queued while Mnemosyne was open")
    args = ap.parse_args()

    if args.flush_queue:
        flush_queue()
        return

    if not args.lang:
        ap.error("--lang is required (e.g. --lang el)")

    token = load_token()
    lingqs = fetch_lingqs(token, args.lang, args.n, args.status)
    print(f"Fetched {len(lingqs)} LingQ(s) for {args.lang}")

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
        write_tsv(lingqs, args.tsv, args.lang,
                  style=args.style, reverse=args.reverse, html=args.html)
        return

    import_to_mnemosyne(lingqs, args.lang,
                        style=args.style, reverse=args.reverse, html=args.html)


if __name__ == "__main__":
    main()
