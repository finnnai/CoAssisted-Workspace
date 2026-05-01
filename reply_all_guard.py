# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Reply-all guard — detect when reply-all is unnecessary before send.

Pure-logic core (no Gmail API). The MCP tool wrapper lives in
tools/reply_all_guard.py and feeds drafts into score_draft().

Detection signals (P0 — keep it simple):

  1. Single-target greeting: body starts with "Hi/Hey/Hello <Name>,"
     and that name maps to exactly one recipient. The other To/CC
     people aren't being addressed.
  2. Ack-only content: body is short (≤ N words) and matches an ack
     pattern ("thanks", "got it", "ok", "noted", "+1", "👍").
  3. FYI content: body contains "FYI" / "for your info" near the top.
  4. CC-fanout: To has 1 person, CC has many — likely a polite CC for
     awareness, but the actual reply is between you and the To.

Verdict:
  - "safe"  — no concerns, send as-is
  - "warn"  — at least one signal fired; show a prompt
  - "block" — strong single-target greeting + ack content; almost
              certainly unintended reply-all

The caller is responsible for the actual send; this module never
mutates Gmail state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Word count below which a body is "ack-only" (regardless of pattern).
ACK_MAX_WORDS = 8

# Quick-ack regex — matches short standalone affirmations.
_ACK_PATTERNS = [
    r"^\s*(thanks?|thx|ty)[\s!,.\-]*$",
    r"^\s*got\s+it[\s!,.\-]*$",
    r"^\s*(ok|okay|k)[\s!,.\-]*$",
    r"^\s*noted[\s!,.\-]*$",
    r"^\s*ack(nowledged)?[\s!,.\-]*$",
    r"^\s*sounds?\s+good[\s!,.\-]*$",
    r"^\s*\+1[\s!,.\-]*$",
    r"^\s*(perfect|great|awesome)[\s!,.\-]*$",
    r"^\s*👍[\s!,.\-]*$",
    r"^\s*will\s+do[\s!,.\-]*$",
    r"^\s*on\s+it[\s!,.\-]*$",
]
_ACK_REGEX = [re.compile(p, re.IGNORECASE) for p in _ACK_PATTERNS]

# FYI patterns — must be near the top to count.
_FYI_REGEX = re.compile(
    r"^\s*(fyi|for\s+your\s+(info(rmation)?|awareness|reference))\b",
    re.IGNORECASE,
)

# Greeting line — "Hi Sarah,", "Hey @amanda", "Hello Brian and Conor,"
# Captures the name(s) after the greeting verb.
_GREETING_REGEX = re.compile(
    r"^\s*(hi|hey|hello|good\s+(morning|afternoon|evening))[\s,]+"
    r"([a-z][\w\s,&/]*?)(?:[,:!.\-—]|$)",
    re.IGNORECASE,
)

# Junk-words that can appear inside a greeting capture but aren't names.
_GREETING_NOISE = {"all", "team", "everyone", "folks", "and", "&"}


@dataclass
class Signal:
    """One detection signal that fired during scoring."""
    code: str          # short stable id e.g. "single_target_greeting"
    message: str       # human-readable explanation
    severity: str      # "info" | "warn" | "strong"


@dataclass
class GuardVerdict:
    """Result of scoring a draft."""
    verdict: str                       # "safe" | "warn" | "block"
    signals: list[Signal] = field(default_factory=list)
    suggested_to: list[str] = field(default_factory=list)
    suggested_cc: list[str] = field(default_factory=list)
    addressed_recipient: str | None = None  # the one person greeted by name

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "signals": [
                {"code": s.code, "message": s.message, "severity": s.severity}
                for s in self.signals
            ],
            "suggested_to": self.suggested_to,
            "suggested_cc": self.suggested_cc,
            "addressed_recipient": self.addressed_recipient,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_addr(addr: str) -> str:
    """Lowercase + strip the email out of 'Display Name <email@dom.com>'."""
    if not addr:
        return ""
    addr = addr.strip()
    # Pull from angle brackets if present.
    m = re.search(r"<([^>]+)>", addr)
    if m:
        return m.group(1).strip().lower()
    return addr.lower()


def _local_part(addr: str) -> str:
    """Local-part of an email, lowercased. 'sarah.fields@example.com' → 'sarah.fields'."""
    addr = _normalize_addr(addr)
    return addr.split("@", 1)[0]


def _display_name(addr: str) -> str | None:
    """Pull display name from 'Sarah Fields <sarah@example.com>' style. None if no name."""
    if not addr or "<" not in addr:
        return None
    name = addr.split("<", 1)[0].strip().strip('"').strip("'")
    return name or None


def _first_name_candidates(addr: str) -> list[str]:
    """Possible first names for a recipient — display name and local-part both fed in."""
    out: list[str] = []
    disp = _display_name(addr)
    if disp:
        out.append(disp.split()[0])
    local = _local_part(addr)
    if local:
        # 'sarah.fields' → 'sarah'; 'sf' stays 'sf'
        out.append(re.split(r"[.\-_+]", local, maxsplit=1)[0])
    return [n.lower() for n in out if n]


def _extract_greeting_names(body: str) -> list[str]:
    """Pull the name(s) from a greeting line. Empty list if no greeting."""
    if not body:
        return []
    first_line = body.strip().splitlines()[0]
    m = _GREETING_REGEX.match(first_line)
    if not m:
        return []
    raw = m.group(3) or ""
    # Split on common delimiters: comma, ampersand, slash, the word "and".
    parts = re.split(r"[,&/]|\band\b", raw, flags=re.IGNORECASE)
    cleaned: list[str] = []
    for p in parts:
        p = p.strip().strip("@").lower()
        if not p or p in _GREETING_NOISE:
            continue
        # Take only the first token (so "Sarah from finance" → "sarah").
        first = p.split()[0]
        cleaned.append(first)
    return cleaned


def _is_ack_body(body: str) -> bool:
    """True if the whole body is essentially an ack."""
    if not body:
        return False
    stripped = body.strip()
    # If body is very short, treat as ack regardless of exact pattern.
    if len(stripped.split()) <= ACK_MAX_WORDS:
        # Match against patterns to be sure it's an ack not a real short reply.
        for rx in _ACK_REGEX:
            if rx.match(stripped):
                return True
        # Short body with no real content (≤ 3 words and no question mark)
        # is functionally an ack too.
        if len(stripped.split()) <= 3 and "?" not in stripped:
            return True
    return False


def _starts_with_fyi(body: str) -> bool:
    if not body:
        return False
    first_line = body.strip().splitlines()[0]
    return bool(_FYI_REGEX.match(first_line))


# --------------------------------------------------------------------------- #
# Core scorer
# --------------------------------------------------------------------------- #


def score_draft(
    body: str,
    to: Iterable[str],
    cc: Iterable[str] | None = None,
    sender: str | None = None,
) -> GuardVerdict:
    """Score a draft for unnecessary reply-all.

    Args:
        body: plain-text body of the draft (no HTML).
        to: list of To: recipients (raw "Name <email>" strings ok).
        cc: list of CC: recipients.
        sender: the user's own address. Excluded from "addressed" matching.

    Returns:
        GuardVerdict with verdict + signals + suggested recipient lists.
    """
    to_list = [a for a in (to or []) if a]
    cc_list = [a for a in (cc or []) if a]
    all_recipients = to_list + cc_list
    sender_norm = _normalize_addr(sender) if sender else None

    # Filter out sender from "people being addressed" calculations.
    recipients_excl_self = [
        a for a in all_recipients
        if _normalize_addr(a) != sender_norm
    ]

    signals: list[Signal] = []
    addressed: str | None = None
    suggested_to = list(to_list)
    suggested_cc = list(cc_list)

    # Only run the guard at all if there's actually a fanout to worry about.
    if len(recipients_excl_self) <= 1:
        return GuardVerdict(
            verdict="safe",
            signals=[Signal("single_recipient", "Only one recipient — guard is a no-op.", "info")],
            suggested_to=suggested_to,
            suggested_cc=suggested_cc,
        )

    # ---- Signal 1: greeting names exactly one recipient
    greeting_names = _extract_greeting_names(body)
    if greeting_names:
        # Try to map every greeting name to a recipient.
        matched: list[str] = []
        for gname in greeting_names:
            for r in recipients_excl_self:
                cands = _first_name_candidates(r)
                if gname in cands:
                    matched.append(r)
                    break
        # Deduplicate while preserving order.
        seen = set()
        matched_unique = []
        for r in matched:
            key = _normalize_addr(r)
            if key not in seen:
                seen.add(key)
                matched_unique.append(r)

        if len(matched_unique) == 1 and len(recipients_excl_self) >= 2:
            addressed = matched_unique[0]
            signals.append(Signal(
                code="single_target_greeting",
                message=(
                    f"Body greets '{greeting_names[0]}' but the email goes to "
                    f"{len(recipients_excl_self)} people. Likely meant for "
                    f"{addressed} only."
                ),
                severity="strong",
            ))
            suggested_to = [addressed]
            suggested_cc = []

    # ---- Signal 2: ack-only body
    ack = _is_ack_body(body)
    if ack:
        signals.append(Signal(
            code="ack_only_body",
            message="Body is an acknowledgement. Reply-all rarely adds value here.",
            severity="warn",
        ))

    # ---- Signal 3: FYI content
    if _starts_with_fyi(body):
        signals.append(Signal(
            code="fyi_body",
            message="Body opens with FYI — typically informational, not deliberative.",
            severity="info",
        ))

    # ---- Signal 4: CC-fanout (1 To, several CC)
    if len(to_list) == 1 and len(cc_list) >= 3:
        signals.append(Signal(
            code="cc_fanout",
            message=(
                f"To has 1 recipient, CC has {len(cc_list)}. The reply may not "
                "need to go to everyone you were CC'd on."
            ),
            severity="info",
        ))

    # ---- Verdict synthesis
    has_strong = any(s.severity == "strong" for s in signals)
    has_warn = any(s.severity in ("warn", "strong") for s in signals)
    ack_with_fanout = ack and len(recipients_excl_self) >= 2

    if has_strong and ack:
        # Single-target greeting + ack content = block.
        verdict = "block"
    elif has_strong or ack_with_fanout:
        verdict = "warn"
    elif has_warn:
        verdict = "warn"
    elif signals:
        verdict = "warn"
    else:
        verdict = "safe"

    return GuardVerdict(
        verdict=verdict,
        signals=signals,
        suggested_to=suggested_to,
        suggested_cc=suggested_cc,
        addressed_recipient=addressed,
    )
