# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P7 — Knowledge layer (2 workflows).

  - #19 Personal wiki from email — keyword-indexed search across pre-fetched threads
  - #46 'What changed?' diff alert — plain-English diff summary for Doc revisions

Notes on #19: A real implementation would use vector embeddings. For the
free-tier path we ship a keyword-based inverted index that's still useful
for "find that thing about Q3 strategy" queries. The wrapper accepts pre-
fetched thread bodies; embedding-based retrieval can be plugged in later.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional


# --------------------------------------------------------------------------- #
# #19 Personal wiki — keyword-indexed thread search
# --------------------------------------------------------------------------- #


# Common English stop words excluded from the index.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to",
    "in", "on", "at", "by", "for", "with", "as", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "i",
    "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "this", "that", "these", "those", "my", "your", "our", "their", "his",
    "its", "from", "so", "not", "no", "yes", "ok", "thanks", "please", "re",
    "fwd", "via", "cc", "hi", "hey", "hello", "best", "regards", "thx",
})

_TOKEN_REGEX = re.compile(r"[A-Za-z][A-Za-z0-9'\-]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, drop stopwords, return the token list."""
    if not text:
        return []
    raw = _TOKEN_REGEX.findall(text.lower())
    return [t for t in raw if t not in _STOPWORDS and len(t) > 1]


@dataclass
class Passage:
    """One scoring unit — a document slice + provenance."""
    thread_id: str
    subject: str
    snippet: str
    score: float
    matched_terms: list[str] = field(default_factory=list)
    timestamp: Optional[str] = None
    link: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "subject": self.subject,
            "snippet": self.snippet,
            "score": round(self.score, 3),
            "matched_terms": list(self.matched_terms),
            "timestamp": self.timestamp,
            "link": self.link,
        }


@dataclass
class WikiIndex:
    """Inverted index over a set of pre-fetched threads."""
    # term → list of (thread_id, term_count_in_thread)
    postings: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    # thread_id → metadata (subject, body, timestamp, link, total_terms)
    threads: dict[str, dict] = field(default_factory=dict)
    total_threads: int = 0

    def to_dict(self) -> dict:
        return {
            "total_threads": self.total_threads,
            "vocabulary_size": len(self.postings),
        }


def build_wiki_index(threads: Iterable[dict]) -> WikiIndex:
    """Build an inverted index over a list of thread dicts.

    Each thread dict must include: id, subject, body. Optional: timestamp, link.
    """
    idx = WikiIndex()
    for t in threads:
        tid = t.get("id")
        if not tid:
            continue
        body = t.get("body") or ""
        subject = t.get("subject") or ""
        # Subject terms get triple weight (more salient than body).
        subject_tokens = tokenize(subject)
        body_tokens = tokenize(body)
        weighted = subject_tokens * 3 + body_tokens
        if not weighted:
            continue
        counts = Counter(weighted)
        idx.threads[tid] = {
            "subject": subject,
            "body": body,
            "timestamp": t.get("timestamp"),
            "link": t.get("link"),
            "total_terms": sum(counts.values()),
        }
        for term, count in counts.items():
            idx.postings.setdefault(term, []).append((tid, count))
        idx.total_threads += 1
    return idx


def search_wiki(
    index: WikiIndex,
    query: str,
    *,
    limit: int = 10,
    snippet_chars: int = 220,
) -> list[Passage]:
    """Search the index. Returns top `limit` passages by TF-IDF score.

    Simple TF-IDF: score = sum_t (tf_thread(t) * idf(t)) for each query term t.
    """
    q_terms = tokenize(query)
    if not q_terms or not index.threads:
        return []
    import math
    n = max(1, index.total_threads)

    # Compute idf per query term
    idf = {}
    for t in q_terms:
        df = len(index.postings.get(t, []))
        # Smoothed idf
        idf[t] = math.log((n + 1) / (df + 1)) + 1.0

    # Accumulate scores per thread
    scores: dict[str, float] = defaultdict(float)
    matched: dict[str, set[str]] = defaultdict(set)
    for t in q_terms:
        for tid, tf in index.postings.get(t, []):
            total_terms = max(1, index.threads[tid]["total_terms"])
            scores[tid] += (tf / total_terms) * idf[t]
            matched[tid].add(t)

    if not scores:
        return []

    # Build passages
    results: list[Passage] = []
    for tid, score in scores.items():
        thread = index.threads[tid]
        snippet = _snippet_around(
            thread["body"], list(matched[tid]), snippet_chars,
        )
        results.append(Passage(
            thread_id=tid,
            subject=thread["subject"],
            snippet=snippet,
            score=score,
            matched_terms=sorted(matched[tid]),
            timestamp=thread.get("timestamp"),
            link=thread.get("link"),
        ))
    results.sort(key=lambda p: -p.score)
    return results[:limit]


def _snippet_around(body: str, terms: list[str], snippet_chars: int) -> str:
    """Pull a snippet of body around the first matched term."""
    if not body or not terms:
        return (body or "")[:snippet_chars]
    body_lower = body.lower()
    earliest = len(body)
    for t in terms:
        idx = body_lower.find(t.lower())
        if 0 <= idx < earliest:
            earliest = idx
    if earliest == len(body):
        return body[:snippet_chars]
    half = snippet_chars // 2
    start = max(0, earliest - half)
    end = min(len(body), earliest + half)
    snippet = body[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(body):
        snippet = snippet + "..."
    return snippet


# --------------------------------------------------------------------------- #
# #46 'What changed?' diff alert
# --------------------------------------------------------------------------- #


@dataclass
class DocDiff:
    """A diff between two versions of a doc body."""
    lines_added: list[str]
    lines_removed: list[str]
    lines_modified: list[tuple[str, str]]  # (before, after) pairs
    summary_bullets: list[str]
    severity: str   # "minor" | "moderate" | "major"

    def to_dict(self) -> dict:
        return {
            "lines_added": list(self.lines_added),
            "lines_removed": list(self.lines_removed),
            "lines_modified": [{"before": b, "after": a} for b, a in self.lines_modified],
            "summary_bullets": list(self.summary_bullets),
            "severity": self.severity,
        }


def diff_doc_text(before: str, after: str,
                  *, ignore_whitespace: bool = True) -> DocDiff:
    """Compute a structured diff between two doc versions.

    Returns line-level adds/removes plus a plain-English summary bullets list.
    Severity ratings:
      - "minor":    < 5 lines changed
      - "moderate": 5-25 lines changed
      - "major":    > 25 lines changed OR any line >100 chars changed
    """
    def normalize(s: str) -> list[str]:
        lines = (s or "").splitlines()
        if ignore_whitespace:
            return [l.strip() for l in lines if l.strip()]
        return lines

    before_lines = normalize(before)
    after_lines = normalize(after)

    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    added: list[str] = []
    removed: list[str] = []
    modified: list[tuple[str, str]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added.extend(after_lines[j1:j2])
        elif tag == "delete":
            removed.extend(before_lines[i1:i2])
        elif tag == "replace":
            # pair them up where possible
            for k in range(max(i2 - i1, j2 - j1)):
                b = before_lines[i1 + k] if i1 + k < i2 else ""
                a = after_lines[j1 + k] if j1 + k < j2 else ""
                if b and a:
                    modified.append((b, a))
                elif a:
                    added.append(a)
                elif b:
                    removed.append(b)

    # Severity
    total_changed = len(added) + len(removed) + len(modified)
    big_line = any(
        len(b) > 100 or len(a) > 100 for b, a in modified
    ) or any(len(l) > 100 for l in added + removed)
    if total_changed == 0:
        severity = "minor"
    elif total_changed > 25 or big_line:
        severity = "major"
    elif total_changed >= 5:
        severity = "moderate"
    else:
        severity = "minor"

    # Summary bullets — first few of each category, kept terse
    summary: list[str] = []
    if added:
        for line in added[:3]:
            summary.append(f"Added: \"{_truncate(line, 80)}\"")
    if removed:
        for line in removed[:3]:
            summary.append(f"Removed: \"{_truncate(line, 80)}\"")
    if modified:
        for b, a in modified[:3]:
            summary.append(
                f"Changed: \"{_truncate(b, 50)}\" → \"{_truncate(a, 50)}\""
            )

    if not summary:
        summary.append("No substantive changes.")

    return DocDiff(
        lines_added=added,
        lines_removed=removed,
        lines_modified=modified,
        summary_bullets=summary,
        severity=severity,
    )


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[:n - 1] + "…"
