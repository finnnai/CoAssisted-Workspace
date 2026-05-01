#!/usr/bin/env python3
"""Refresh brand voice guidelines from the user's recent sent mail.

The output is a Markdown document at `brand-voice.md` in the project folder.
That doc is the source of truth for the brand-voice:enforce-voice skill — when
you ask Claude to "write an email in my voice", it reads brand-voice.md and
applies it.

Approach:
    1. Pull `in:sent newer_than:<days>d` (default 90).
    2. For each message, extract just the prose YOU wrote — strip quoted
       reply content (lines after "On X wrote:" etc.) and signature blocks.
    3. Analyze the corpus:
         - Heuristic mode (default if no ANTHROPIC_API_KEY): extract sign-offs,
           openers, sentence-length distribution, formality markers, top
           distinctive vocabulary. Free, deterministic.
         - LLM mode (when ANTHROPIC_API_KEY is set, opt-in via --mode=llm or
           --mode=auto): send batched samples to Claude for a richer markdown
           voice guide. ~$0.05-0.15 per run.
    4. Write `brand-voice.md` with a timestamp + source-message count.

Designed for a quarterly cron:
    0 6 1 */3 *  /path/to/.venv/bin/python /path/to/refresh_brand_voice.py

Defaults work without an API key. With one set, the output is much sharper.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import re
import sys
from pathlib import Path

import gservices
import llm
from auth import get_credentials  # noqa: F401 — implicit auth check via gservices
from logging_util import log
from tools.enrichment import _extract_plaintext_body, _isolate_signature, _strip_quoted


_PROJECT_DIR = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_DIR / "logs"
_OUTPUT_PATH = _PROJECT_DIR / "brand-voice.md"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _rotate_cron_log(max_mb: int = 10) -> None:
    """Same convention as refresh_stats / enrich_inbox: trim the cron log."""
    log_path = _LOG_DIR / f"{Path(__file__).stem}.cron.log"
    if not log_path.exists():
        return
    if log_path.stat().st_size > max_mb * 1024 * 1024:
        backup = log_path.with_suffix(".cron.log.old")
        try:
            if backup.exists():
                backup.unlink()
            log_path.rename(backup)
            log.info("refresh_brand_voice: rotated cron log (was > %d MB)", max_mb)
        except Exception as e:
            log.warning("refresh_brand_voice: cron log rotation failed: %s", e)


_GREETING_RX = re.compile(
    r"^(?:hi|hey|hello|dear|good\s+(?:morning|afternoon|evening))\b[^,!]*[,!]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_my_own_sig(prose: str) -> str:
    """Remove what looks like the sender's own signature block at the end.

    Reuses _isolate_signature; if a sig is detected, slice it off so the
    remaining text is just what the user wrote.
    """
    sig = _isolate_signature(prose)
    if sig and sig in prose:
        idx = prose.rfind(sig)
        if idx >= 0:
            return prose[:idx].rstrip()
    return prose


def _is_google_auto_body(prose: str) -> bool:
    """True if the body matches a Google auto-generated template pattern.

    These appear in your sent folder because Google attributes them to you
    (Drive shares, Meet links, Forms responses, etc.) but contain no prose
    you actually wrote. They poison brand-voice analysis.
    """
    head = prose.strip()[:300].lower()
    patterns = (
        "has shared the following",
        "shared a folder with you",
        "shared a document with you",
        "has invited you to edit",
        "has invited you to view",
        "has invited you to comment",
        "you've been invited to",
        "you have been invited to",
        "this is a reminder for the upcoming event",
        "is video calling you",
        "join with google meet",
        "view in google docs",
        "view in google sheets",
        "view in google slides",
        "responded to your form",
        "your form has been submitted",
        "your meeting transcript",
    )
    return any(pat in head for pat in patterns)


def fetch_sent_prose(days: int, limit: int) -> tuple[list[str], dict]:
    """Pull sent mail and return (prose_samples, metadata).

    metadata: {scanned, kept, oldest_iso, newest_iso}
    """
    gmail = gservices.gmail()
    # Exclude Calendar invites and meeting RSVPs — they appear in sent folder
    # with you as the From address (Google auto-generates them on your behalf)
    # but the body is template boilerplate, not authored prose. Drive shares
    # and Meet invites are filtered downstream by body-pattern matching since
    # they don't have a clean subject prefix. Override with
    # BRAND_VOICE_INCLUDE_AUTO=1 to disable both gates.
    import os as _os
    _exclude_subject = (
        ""
        if _os.environ.get("BRAND_VOICE_INCLUDE_AUTO") == "1"
        else (
            ' -subject:"Invitation:"'
            ' -subject:"Updated invitation:"'
            ' -subject:"Canceled event:"'
            ' -subject:"Accepted:"'
            ' -subject:"Tentative:"'
            ' -subject:"Declined:"'
        )
    )
    query = f"in:sent newer_than:{days}d{_exclude_subject}"
    log.info("refresh_brand_voice: fetching %s (limit=%d)", query, limit)

    message_ids: list[str] = []
    page_token = None
    while len(message_ids) < limit:
        kwargs: dict = {
            "userId": "me",
            "q": query,
            "maxResults": min(500, limit - len(message_ids)),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = gmail.users().messages().list(**kwargs).execute()
        batch = resp.get("messages", []) or []
        message_ids.extend(m["id"] for m in batch)
        page_token = resp.get("nextPageToken")
        if not page_token or not batch:
            break

    log.info("refresh_brand_voice: scanning %d messages", len(message_ids))

    samples: list[str] = []
    timestamps: list[int] = []
    for mid in message_ids:
        try:
            msg = gmail.users().messages().get(
                userId="me", id=mid, format="full"
            ).execute()
        except Exception as e:
            log.warning("refresh_brand_voice: fetch %s failed: %s", mid, e)
            continue
        ts = int(msg.get("internalDate", "0"))
        body = _extract_plaintext_body(msg.get("payload") or {})
        # Strip quoted reply content (preserves what YOU wrote).
        clean = _strip_quoted(body)
        # Strip your own signature block at the end so we don't learn from sigs.
        clean = _strip_my_own_sig(clean)
        # Skip near-empty messages — too little signal.
        if len(clean.strip()) < 50:
            continue
        # Skip Google auto-generated bodies (Drive shares, Meet invites,
        # Forms responses) that pass in:sent because Google attributes them
        # to you. They're template boilerplate, not authored prose.
        if _is_google_auto_body(clean):
            continue
        # Trim to a sensible per-message cap so a single huge email doesn't
        # dominate the corpus.
        samples.append(clean.strip()[:4000])
        timestamps.append(ts)

    meta = {
        "scanned": len(message_ids),
        "kept": len(samples),
        "oldest_iso": (
            datetime.datetime.fromtimestamp(
                min(timestamps) / 1000, tz=datetime.timezone.utc,
            ).isoformat().replace("+00:00", "Z")
            if timestamps else None
        ),
        "newest_iso": (
            datetime.datetime.fromtimestamp(
                max(timestamps) / 1000, tz=datetime.timezone.utc,
            ).isoformat().replace("+00:00", "Z")
            if timestamps else None
        ),
    }
    return samples, meta


# --------------------------------------------------------------------------- #
# Heuristic analyzer
# --------------------------------------------------------------------------- #


_SIGNOFF_PHRASES = [
    "best", "best regards", "thanks", "thanks!", "thank you", "cheers",
    "sincerely", "warmly", "kindly", "talk soon", "speak soon",
    "all the best", "yours", "yours truly", "regards", "appreciated",
    "thanks so much", "many thanks", "much appreciated",
]


def _detect_signoff(prose: str) -> str | None:
    """Detect a sign-off phrase from the last few lines."""
    last_lines = [ln.strip() for ln in prose.splitlines()[-5:] if ln.strip()]
    for ln in reversed(last_lines):
        ln_lc = ln.lower().rstrip(",.!")
        for phrase in _SIGNOFF_PHRASES:
            if ln_lc == phrase or ln_lc.startswith(phrase + ","):
                return phrase
    return None


def _detect_opener(prose: str) -> str | None:
    """First content line — return a normalized opener key."""
    for ln in prose.splitlines():
        s = ln.strip()
        if not s:
            continue
        # Match "Hi <name>", "Hey", "Thanks for ...", "Quick question", etc.
        m = re.match(
            r"(?i)^("
            r"hi|hey|hello|good (?:morning|afternoon|evening)|"
            r"thanks(?: for)?|thank you(?: for)?|"
            r"quick question|just (?:wanted|checking)|"
            r"following up|circling back|"
            r"appreciate|happy to)\b",
            s,
        )
        if m:
            return m.group(1).lower()
        return None
    return None


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "to", "of", "in",
    "on", "at", "by", "for", "with", "from", "as", "this", "that", "these",
    "those", "it", "its", "we", "us", "our", "you", "your", "they", "them",
    "their", "i", "me", "my", "he", "him", "his", "she", "her", "if",
    "then", "so", "not", "no", "yes", "any", "all", "some", "more", "most",
    "than", "into", "out", "up", "down", "about", "just", "like", "also",
    "what", "when", "where", "who", "why", "how", "which", "there", "here",
    "now", "today", "yesterday", "tomorrow", "very", "much", "lot", "well",
}


def heuristic_analyze(samples: list[str]) -> dict:
    """Return a dict of stats useful for brand-voice.md."""
    signoffs = collections.Counter()
    openers = collections.Counter()
    sentence_lengths: list[int] = []
    contractions_count = 0
    fullforms_count = 0
    em_dash_count = 0
    excl_count = 0
    semi_count = 0
    word_freq = collections.Counter()

    contraction_rx = re.compile(
        r"\b(?:i'm|you're|we're|they're|it's|that's|don't|doesn't|didn't|"
        r"won't|can't|haven't|hasn't|hadn't|wouldn't|shouldn't|couldn't|"
        r"i've|you've|we've|they've|i'll|you'll|we'll|they'll)\b",
        re.IGNORECASE,
    )
    fullform_rx = re.compile(
        r"\b(?:i am|you are|we are|they are|it is|that is|do not|does not|"
        r"did not|will not|cannot|have not|has not|had not|would not|"
        r"should not|could not|i have|you have|we have|they have|i will|"
        r"you will|we will|they will)\b",
        re.IGNORECASE,
    )

    for sample in samples:
        s = sample.strip()
        if not s:
            continue

        if signoff := _detect_signoff(s):
            signoffs[signoff] += 1
        if opener := _detect_opener(s):
            openers[opener] += 1

        # Sentence stats (rough split on .!?).
        sentences = [seg.strip() for seg in re.split(r"[.!?]+\s+", s) if seg.strip()]
        for sent in sentences:
            words = sent.split()
            if 1 <= len(words) <= 80:
                sentence_lengths.append(len(words))

        contractions_count += len(contraction_rx.findall(s))
        fullforms_count += len(fullform_rx.findall(s))
        em_dash_count += s.count("—") + s.count(" -- ")
        excl_count += s.count("!")
        semi_count += s.count(";")

        # Vocabulary: lowercase words, alpha only, skip stopwords + 2-letter words.
        for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", s.lower()):
            if w in _STOPWORDS:
                continue
            word_freq[w] += 1

    avg_sentence = (sum(sentence_lengths) / len(sentence_lengths)) if sentence_lengths else 0
    short_pct = (
        sum(1 for n in sentence_lengths if n < 8) * 100 / len(sentence_lengths)
        if sentence_lengths else 0
    )
    long_pct = (
        sum(1 for n in sentence_lengths if n > 20) * 100 / len(sentence_lengths)
        if sentence_lengths else 0
    )
    contraction_ratio = (
        contractions_count / max(1, contractions_count + fullforms_count)
    )

    return {
        "sample_count": len(samples),
        "top_signoffs": signoffs.most_common(8),
        "top_openers": openers.most_common(8),
        "avg_sentence_words": round(avg_sentence, 1),
        "short_sentence_pct": round(short_pct, 1),
        "long_sentence_pct": round(long_pct, 1),
        "contraction_ratio": round(contraction_ratio, 2),
        "em_dash_count": em_dash_count,
        "excl_count": excl_count,
        "semicolon_count": semi_count,
        "top_words": word_freq.most_common(40),
    }


# --------------------------------------------------------------------------- #
# LLM analyzer
# --------------------------------------------------------------------------- #


_LLM_SYSTEM = """You are a brand-voice analyst. The user has shared samples of \
emails they personally wrote. Your job: extract their voice and produce a \
Markdown style guide that another person (or AI) can follow to write in that \
same voice convincingly.

Be specific and observational, not generic. Quote actual phrases the user uses. \
Note their habits — punctuation choices, sentence rhythm, level of formality, \
how they open/close messages, what they avoid. Avoid vague advice like \
"be professional"; instead say "uses em-dashes for asides, rarely uses \
exclamation points except in subject lines"."""


_LLM_USER_TEMPLATE = """Below are {n} samples of emails I wrote in the last \
{days} days. The samples are separated by `---`. Quoted reply content and my \
own signature have already been stripped — what's left is just what I wrote.

Produce a Markdown brand-voice guide with these sections:
1. **Voice in one paragraph** — overall feel
2. **Tone & formality** — where on the spectrum, with examples
3. **Sentence rhythm** — typical lengths, structures, openers
4. **Sign-offs & openers** — common patterns I actually use
5. **Vocabulary & phrasing** — distinctive word choices, recurring phrases
6. **Punctuation habits** — em-dashes, exclamation points, semicolons, etc.
7. **What I avoid** — patterns notably absent
8. **Three do/don't pairs** — concrete contrasts to follow

Quote my actual phrasing whenever possible. Be honest if some sections have \
weak signal in the samples.

---

{samples}"""


def llm_analyze(samples: list[str], days: int) -> str:
    """Send samples to Claude and return Markdown guidelines.

    We trim the corpus to a token-budget-friendly size before sending. Most
    accounts won't have 100s of long emails, but we cap conservatively.
    """
    # Cap total chars to ~80K (≈ 20K tokens for Claude); covers ~50-100 emails.
    budget = 80_000
    trimmed: list[str] = []
    used = 0
    for s in samples:
        if used + len(s) > budget:
            break
        trimmed.append(s)
        used += len(s)
    log.info(
        "refresh_brand_voice: sending %d/%d samples to Claude (%d chars)",
        len(trimmed), len(samples), used,
    )

    joined = "\n\n---\n\n".join(trimmed)
    prompt = _LLM_USER_TEMPLATE.format(n=len(trimmed), days=days, samples=joined)
    result = llm.call_simple(
        prompt,
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=_LLM_SYSTEM,
        temperature=0.2,
    )
    log.info(
        "refresh_brand_voice: LLM call done — %d in / %d out tokens, ~$%s",
        result["input_tokens"], result["output_tokens"], result["estimated_cost_usd"],
    )
    return result["text"]


# --------------------------------------------------------------------------- #
# Output rendering
# --------------------------------------------------------------------------- #


def _render_heuristic_md(analysis: dict, meta: dict, days: int) -> str:
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    sig_lines = "\n".join(
        f"- **{s.title()}** — used {n} times" for s, n in analysis["top_signoffs"]
    ) or "_(no clear pattern detected)_"
    open_lines = "\n".join(
        f"- **{o.title()}** — used {n} times" for o, n in analysis["top_openers"]
    ) or "_(no clear pattern detected)_"
    word_lines = "\n".join(
        f"- {w} ({n})" for w, n in analysis["top_words"][:25]
    )
    out = f"""# Brand Voice Guidelines

_Auto-generated from your last {days} days of sent mail. Do not hand-edit
above the divider — re-run `refresh_brand_voice.py` to regenerate. Hand-edits
below the divider are preserved._

**Generated:** {now}
**Source:** {analysis['sample_count']} sent emails (scanned {meta['scanned']}, kept {meta['kept']})
**Date range:** {meta['oldest_iso']} → {meta['newest_iso']}
**Mode:** heuristic (no LLM API key — set `anthropic_api_key` in config.json
or export `ANTHROPIC_API_KEY` for richer analysis)

---

## Sentence rhythm

- Average sentence length: **{analysis['avg_sentence_words']} words**
- Short sentences (< 8 words): **{analysis['short_sentence_pct']}%**
- Long sentences (> 20 words): **{analysis['long_sentence_pct']}%**

## Sign-offs

{sig_lines}

## Openers

{open_lines}

## Punctuation habits

- Contraction ratio: **{int(analysis['contraction_ratio'] * 100)}%** contracted vs full forms
- Em-dashes used: **{analysis['em_dash_count']}**
- Exclamation points: **{analysis['excl_count']}**
- Semicolons: **{analysis['semicolon_count']}**

## Distinctive vocabulary

Top recurring words (excluding common stopwords):

{word_lines}

---

## Hand-edits (preserved across regeneration)

_Add overrides, exceptions, or context here. The `brand-voice:enforce-voice`
skill reads everything in this file._
"""
    return out


def _render_llm_md(llm_text: str, meta: dict, days: int, sample_count: int) -> str:
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    out = f"""# Brand Voice Guidelines

_Auto-generated from your last {days} days of sent mail. Do not hand-edit
above the divider — re-run `refresh_brand_voice.py` to regenerate. Hand-edits
below the divider are preserved._

**Generated:** {now}
**Source:** {sample_count} sent emails (scanned {meta['scanned']}, kept {meta['kept']})
**Date range:** {meta['oldest_iso']} → {meta['newest_iso']}
**Mode:** LLM analysis (claude-sonnet-4-6)

---

{llm_text}

---

## Hand-edits (preserved across regeneration)

_Add overrides, exceptions, or context here. The `brand-voice:enforce-voice`
skill reads everything in this file._
"""
    return out


def _preserve_hand_edits(new_doc: str) -> str:
    """If brand-voice.md exists and has a 'Hand-edits' section, preserve it."""
    if not _OUTPUT_PATH.exists():
        return new_doc
    try:
        existing = _OUTPUT_PATH.read_text(encoding="utf-8")
    except Exception:
        return new_doc
    marker = "## Hand-edits (preserved across regeneration)"
    if marker not in existing:
        return new_doc
    existing_tail = existing.split(marker, 1)[1]
    if marker in new_doc:
        head = new_doc.split(marker, 1)[0]
        return head + marker + existing_tail
    return new_doc


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh brand-voice.md from your sent mail."
    )
    parser.add_argument("--days", type=int, default=90,
                        help="How many days of sent mail to analyze. Default 90.")
    parser.add_argument("--limit", type=int, default=1000,
                        help="Max sent messages to scan. Default 1000.")
    parser.add_argument(
        "--mode", choices=["auto", "heuristic", "llm"], default="auto",
        help=(
            "auto (default): use LLM if ANTHROPIC_API_KEY is set, else heuristic. "
            "heuristic: never call LLM. llm: require LLM (fail if no key)."
        ),
    )
    parser.add_argument("--output", default=str(_OUTPUT_PATH),
                        help=f"Output path for the markdown. Default {_OUTPUT_PATH}.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate but don't overwrite brand-voice.md; print to stdout.")
    parser.add_argument("--max-cron-log-mb", type=int, default=10)
    args = parser.parse_args(argv)

    _rotate_cron_log(args.max_cron_log_mb)

    # Decide LLM availability.
    llm_ok, llm_reason = llm.is_available()
    use_llm = False
    if args.mode == "llm":
        if not llm_ok:
            print(f"--mode=llm requested but LLM unavailable: {llm_reason}",
                  file=sys.stderr)
            return 2
        use_llm = True
    elif args.mode == "auto" and llm_ok:
        use_llm = True

    log.info("refresh_brand_voice: mode=%s use_llm=%s", args.mode, use_llm)

    # Fetch corpus.
    try:
        samples, meta = fetch_sent_prose(days=args.days, limit=args.limit)
    except Exception as e:
        print(f"failed to fetch sent mail: {e}", file=sys.stderr)
        return 3

    if not samples:
        print(
            f"no usable sent mail in the last {args.days} days "
            f"(scanned {meta['scanned']}). Nothing to analyze.",
            file=sys.stderr,
        )
        return 4

    # Analyze. Track actual_mode so we never mislabel a fallback.
    actual_mode = "heuristic"
    if use_llm:
        try:
            llm_text = llm_analyze(samples, days=args.days)
            doc = _render_llm_md(llm_text, meta, args.days, len(samples))
            actual_mode = "llm"
        except Exception as e:
            log.warning("LLM analysis failed (%s) — falling back to heuristic.", e)
            print(f"LLM analysis failed: {e}. Falling back to heuristic.", file=sys.stderr)
            analysis = heuristic_analyze(samples)
            doc = _render_heuristic_md(analysis, meta, args.days)
            actual_mode = "heuristic_after_llm_fail"
    else:
        analysis = heuristic_analyze(samples)
        doc = _render_heuristic_md(analysis, meta, args.days)

    doc = _preserve_hand_edits(doc)

    if args.dry_run:
        print(doc)
        log.info("refresh_brand_voice: DRY-RUN (no file written)")
        return 0

    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    summary = (
        f"refresh_brand_voice done: {len(samples)} samples → {out_path} "
        f"(mode={actual_mode})"
    )
    log.info(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
