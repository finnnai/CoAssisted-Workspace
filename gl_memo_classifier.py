# © 2026 CoAssisted Workspace. Licensed under MIT.
"""GL memo classifier — Tier 2 of the GL classifier ladder.

A dependency-free Naive-Bayes-lite memo matcher trained on the 17,346
posted journal entries in `samples/Wolfhound Corp JEs Jan-Mar'26.xlsx`.

Given a transaction memo (e.g. "AMEX Transactions 03.01.26-03.31.26 -
Szott, Joshua - PIRATE SHIP"), returns the top-k Workday Ledger
Accounts the trained model thinks the line should post to, with a
score per candidate.

Two-piece design:

    1. Training (offline, one-shot):
       `scripts/train_gl_memo_classifier.py`
       Reads the JE Excel, tokenizes Line Memo per row, builds a
       per-token → GL-account count distribution, writes the index to
       `gl_memo_index.json`.

    2. Runtime (this module):
       Loads the index lazily on first call, scores any incoming memo
       via log-additive Naive-Bayes-lite, returns ranked candidates.

The scoring formula is intentionally simple:

    score(GL) = log(P(GL)) + Σ_t log(P(token=t | GL))

With Laplace smoothing on token counts (alpha=1) so tokens never seen
during training don't blow up to -inf. We normalize log scores to a
[0, 1] band per query so the highest-scoring GL gets ~1.0 and
candidates fall off in proportion to log-likelihood gap.

Confidence band returned to the caller depends on the gap between
the top-1 and top-2 scores:

    gap >= 0.30 → MEDIUM confidence (clear winner)
    gap <  0.30 → LOW confidence    (caller should consult Tier 3)

(Tier 1 — MCC table — already returns HIGH for matched MCCs, so this
module never reports HIGH. Tier 0 — operator map — also returns HIGH.
Memo-pattern matching is inherently squishier than either; capping
at MEDIUM forces ambiguous cases into the review queue.)
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional


# =============================================================================
# Storage
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent
_INDEX_PATH = _PROJECT_ROOT / "gl_memo_index.json"

# Lazy-loaded singleton — the index is ~few-MB JSON; we don't want to
# pay the parse cost on every classify_transaction call.
_INDEX: Optional[dict] = None


def _load_index() -> Optional[dict]:
    """Read the trained index off disk. None if not yet trained."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    if not _INDEX_PATH.exists():
        return None
    try:
        with _INDEX_PATH.open("r", encoding="utf-8") as f:
            _INDEX = json.load(f)
        return _INDEX
    except (json.JSONDecodeError, OSError):
        return None


def _reset_for_test() -> None:
    """Clear the in-process cache. Used by tests with monkeypatched paths."""
    global _INDEX
    _INDEX = None


# =============================================================================
# Tokenization — same logic used at training time
# =============================================================================

# Stopwords scrubbed before scoring. Kept short and conservative — we
# want the JE memo signal, not generic English.
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "by",
    "with", "from", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "or", "but", "not", "no",
    "to", "record", "apply", "applied", "applying",
})

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def tokenize(memo: str) -> list[str]:
    """Lowercase + extract alphanumeric tokens + drop stopwords + dedupe.

    Dedup keeps the bag-of-words representation simple — repeated tokens
    in a memo don't double-count. Empty memos return [].
    """
    if not memo:
        return []
    raw = _TOKEN_PATTERN.findall(memo.lower())
    # Filter stopwords + tokens that are too short to be informative.
    seen: set[str] = set()
    out: list[str] = []
    for tok in raw:
        if len(tok) < 2 or tok in _STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


# =============================================================================
# Scoring
# =============================================================================

# Laplace smoothing constant. alpha=1.0 is standard add-one smoothing.
_ALPHA = 1.0


def _score_gl(
    tokens: list[str],
    gl: str,
    token_counts_by_gl: dict[str, int],
    gl_token_total: int,
    gl_prior: int,
    total_documents: int,
    vocab_size: int,
) -> float:
    """Log-likelihood score: log P(GL) + Σ log P(token | GL).

    Args:
        tokens: tokenized query memo
        gl: candidate GL account string
        token_counts_by_gl: count of each token under this specific GL
            (extracted by the caller from the index)
        gl_token_total: total token occurrences across all rows for this GL
        gl_prior: number of training rows that posted to this GL
        total_documents: training set size (denominator for log P(GL))
        vocab_size: vocabulary size (denominator with smoothing)

    Returns:
        Log-additive score; higher = more likely.
    """
    log_prior = math.log(gl_prior / total_documents)
    log_likelihood = 0.0
    for tok in tokens:
        count = token_counts_by_gl.get(tok, 0)
        # Add-one smoothing.
        prob = (count + _ALPHA) / (gl_token_total + _ALPHA * vocab_size)
        log_likelihood += math.log(prob)
    return log_prior + log_likelihood


def lookup_by_memo(
    memo: str,
    *,
    top_k: int = 3,
) -> list[tuple[str, float]]:
    """Return top-k (GL account, normalized score) for a memo.

    Score is normalized to [0, 1] within the result set — the top
    candidate gets 1.0 and the rest are scaled by relative log-likelihood
    gap. This makes scores comparable across different memos even though
    raw log scores aren't.

    Returns [] if the index isn't trained yet, or the memo has no usable
    tokens.
    """
    index = _load_index()
    if not index:
        return []
    tokens = tokenize(memo)
    if not tokens:
        return []

    gl_priors: dict[str, int] = index.get("gl_priors", {})
    tokens_by_gl: dict[str, dict[str, int]] = index.get("tokens_by_gl", {})
    gl_token_totals: dict[str, int] = index.get("gl_token_totals", {})
    total_documents: int = int(index.get("n_documents", 0))
    vocab_size: int = int(index.get("vocab_size", 1))

    if not gl_priors or total_documents == 0:
        return []

    # Score every GL that appears in training. Cheap — we have ~hundreds
    # of distinct GL accounts at most, even with 17k training rows.
    raw_scores: list[tuple[str, float]] = []
    for gl, prior in gl_priors.items():
        if prior <= 0:
            continue
        gl_token_total = gl_token_totals.get(gl, 0)
        gl_token_counts = tokens_by_gl.get(gl, {})
        score = _score_gl(
            tokens=tokens,
            gl=gl,
            token_counts_by_gl=gl_token_counts,
            gl_token_total=gl_token_total,
            gl_prior=prior,
            total_documents=total_documents,
            vocab_size=vocab_size,
        )
        raw_scores.append((gl, score))

    if not raw_scores:
        return []

    # Sort descending, take top-k.
    raw_scores.sort(key=lambda item: item[1], reverse=True)
    top = raw_scores[:top_k]

    # Normalize to [0, 1] using a simple log-gap ratio.
    best_score = top[0][1]
    worst_in_top = top[-1][1]
    span = best_score - worst_in_top
    if span <= 0:
        # All scores identical — return as-is with 1.0 each (rare; typically
        # means all candidates tie because no query tokens were seen at
        # training time).
        return [(gl, 1.0) for gl, _ in top]
    return [
        (gl, (score - worst_in_top) / span)
        for gl, score in top
    ]


def confidence_from_top_two(
    top_results: list[tuple[str, float]],
    *,
    gap_threshold: float = 0.30,
) -> str:
    """Return 'medium' if there's a clear winner, 'low' otherwise.

    Caller (gl_classifier.classify_transaction) wraps the result in the
    Confidence enum.
    """
    if len(top_results) < 2:
        return "low"
    gap = top_results[0][1] - top_results[1][1]
    return "medium" if gap >= gap_threshold else "low"


# =============================================================================
# Diagnostics — for the review queue / admin UI
# =============================================================================

def index_status() -> dict:
    """Return metadata about the loaded index, or a 'not trained' marker.

    Exposed as a workflow tool (`workflow_gl_memo_index_status`) so
    operators can see if/when the model was last trained without
    digging into JSON files.
    """
    index = _load_index()
    if not index:
        return {
            "status": "untrained",
            "message": (
                "No gl_memo_index.json found. Run "
                "`python3 scripts/train_gl_memo_classifier.py` to build it "
                "from samples/Wolfhound Corp JEs Jan-Mar'26.xlsx."
            ),
        }
    return {
        "status": "trained",
        "trained_on": index.get("trained_on"),
        "training_rows": int(index.get("n_documents", 0)),
        "gl_accounts_seen": len(index.get("gl_priors", {})),
        "vocabulary_size": int(index.get("vocab_size", 0)),
    }
