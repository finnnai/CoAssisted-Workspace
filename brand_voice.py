# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Brand voice composer — shared service.

Lifted out of project_invoices (which keeps its AP-specific composer for
backwards compat). Provides general-purpose draft composition for any
audience + intent + content brief, used by the P2 workflows:

  - #15 auto-draft inbound replies
  - #24 conflict-aware RSVPs
  - #25 ghost agendas
  - #26 birthday/anniversary notes
  - #40 introduction follow-ups
  - #43 cross-thread context (passive — no compose, just context)
  - #74 multi-recipient meeting coordinator
  - #77 foreign-language translate + reply

Two paths through this module:
  1. LLM-backed (Anthropic Haiku) — preferred when an API key is configured.
     Loads brand-voice.md as the system prompt, generates real prose.
  2. Template-only fallback — if no LLM is available, slot a few template
     phrases together. Less polished but still on-brand.

The composer is deterministic given the same inputs (LLM is called with
temperature=0). Variant cycling is handled via deterministic hashing.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import llm

_BRAND_VOICE_PATH = Path(__file__).resolve().parent / "brand-voice.md"


# --------------------------------------------------------------------------- #
# Voice loading
# --------------------------------------------------------------------------- #


def load_voice_guide(path: Optional[Path] = None, max_chars: int = 4000) -> str:
    """Read brand-voice.md (or override path) and return up to max_chars of it.
    Empty string if the file doesn't exist — caller falls back to neutral tone.
    """
    p = path or _BRAND_VOICE_PATH
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")[:max_chars]
    except Exception:
        pass
    return ""


# --------------------------------------------------------------------------- #
# Intent + audience taxonomy
# --------------------------------------------------------------------------- #


# Recognized intents — each maps to a high-level prompt scaffold.
INTENTS = {
    "reply",            # general response to inbound mail
    "decline",          # polite decline of an ask or invite
    "nudge",            # gentle follow-up
    "acknowledge",      # confirmation / receipt
    "agenda",           # meeting agenda from context
    "intro_followup",   # nudge after an intro you made
    "birthday",         # birthday / anniversary note
    "rsvp_alternative", # decline + propose alternatives
    "translate_reply",  # respond in same language as inbound
    "scheduling_poll",  # propose 3 time slots to a group
}

# Audience codes flip tone.
AUDIENCES = {"customer", "vendor", "employee", "internal_peer", "personal"}


@dataclass
class DraftRequest:
    """Inputs for one composer call."""
    intent: str
    audience: str = "customer"
    recipient_name: Optional[str] = None
    sender_name: Optional[str] = None
    subject_hint: Optional[str] = None  # for the email subject line
    context: str = ""        # the inbound thread / situation summary
    facts: dict = field(default_factory=dict)  # structured facts to weave in
    constraints: list[str] = field(default_factory=list)
    target_language: Optional[str] = None  # e.g. "fr" for #77
    seed_hint: Optional[str] = None        # used for deterministic variant choice


@dataclass
class DraftOutput:
    """One composed draft ready to send (or queue for review)."""
    subject: str
    plain: str
    html: str
    intent: str
    audience: str
    voice_used: bool          # True if LLM generated; False if template fallback
    variant_seed: str
    estimated_cost_usd: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "plain": self.plain,
            "html": self.html,
            "intent": self.intent,
            "audience": self.audience,
            "voice_used": self.voice_used,
            "variant_seed": self.variant_seed,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _greeting_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = str(name).strip()
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    s = s.strip("\"'")
    if "@" in s:
        return ""
    return s.split()[0] if s else ""


def _seed(req: DraftRequest) -> str:
    """Deterministic hash of input → used to pick variant phrasing.
    Caller can override via req.seed_hint to force a fresh variant."""
    base = (
        (req.seed_hint or "") + req.intent + req.audience
        + (req.recipient_name or "") + (req.subject_hint or "")
    )
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Template fallback (no-LLM path)
# --------------------------------------------------------------------------- #


# Per-intent canned scaffolds. Each is a tuple of (subject_template, body_template).
# Variables: {greeting} {sender} {context_short} {ask}
_TEMPLATES = {
    "reply": (
        "Re: {subject_hint}",
        "{greeting}\n\n{ask}\n\nThanks,\n{sender}",
    ),
    "decline": (
        "Re: {subject_hint}",
        "{greeting}\n\nThanks for reaching out — I'm not going to be able to "
        "do this one.\n\n{ask}\n\nThanks,\n{sender}",
    ),
    "nudge": (
        "Following up on {subject_hint}",
        "{greeting}\n\nQuick nudge on this — {ask}\n\nThanks,\n{sender}",
    ),
    "acknowledge": (
        "Got it — {subject_hint}",
        "{greeting}\n\nGot it, thanks. {ask}\n\n{sender}",
    ),
    "agenda": (
        "Agenda for our meeting",
        "{greeting}\n\nAgenda for our meeting:\n\n{ask}\n\nSee you then,\n{sender}",
    ),
    "intro_followup": (
        "Following up on the intro",
        "{greeting}\n\nFollowing up on the intro a couple weeks back — "
        "{ask}\n\nThanks,\n{sender}",
    ),
    "birthday": (
        "Happy birthday!",
        "{greeting}\n\n{ask}\n\nBest,\n{sender}",
    ),
    "rsvp_alternative": (
        "Re: {subject_hint}",
        "{greeting}\n\nI have a conflict at that time. {ask}\n\n"
        "Let me know which works.\n\nThanks,\n{sender}",
    ),
    "translate_reply": (
        "Re: {subject_hint}",
        "{greeting}\n\n{ask}\n\nThanks,\n{sender}",
    ),
    "scheduling_poll": (
        "Time to meet?",
        "{greeting}\n\n{ask}\n\nLet me know which slot works best.\n\nThanks,\n{sender}",
    ),
}


def _template_compose(req: DraftRequest) -> DraftOutput:
    """Build a draft from canned scaffolds. Used when the LLM is unavailable."""
    subj_tpl, body_tpl = _TEMPLATES.get(req.intent, _TEMPLATES["reply"])
    greeting_name = _greeting_name(req.recipient_name)
    greeting = f"Hi {greeting_name}," if greeting_name else "Hi,"
    sender = _greeting_name(req.sender_name) or req.sender_name or ""
    ask = req.context.strip() or "—"
    plain = body_tpl.format(
        greeting=greeting,
        ask=ask,
        sender=sender,
    )
    subject = subj_tpl.format(subject_hint=req.subject_hint or req.intent)
    html = "<br>".join(plain.splitlines()).replace("\n", "<br>")
    return DraftOutput(
        subject=subject, plain=plain, html=html,
        intent=req.intent, audience=req.audience,
        voice_used=False, variant_seed=_seed(req),
        estimated_cost_usd=0.0,
    )


# --------------------------------------------------------------------------- #
# LLM-backed compose
# --------------------------------------------------------------------------- #


# Per-intent guidance the LLM gets in addition to the brand voice doc.
_INTENT_DIRECTIVES = {
    "reply":            "Respond directly to the inbound message. Match its energy and length. Don't add fluff.",
    "decline":          "Politely decline. Don't over-apologize. Brief reason if natural; no reason if it would feel forced.",
    "nudge":            "Gentle follow-up — never naggy. Imply busy schedules on both sides.",
    "acknowledge":      "Short acknowledgement. 1-3 sentences max. No greeting needed if context is conversational.",
    "agenda":           "Generate a 3-bullet meeting agenda from the context. Tight, specific, action-oriented.",
    "intro_followup":   "Friendly nudge that the intro hasn't gone anywhere. Offer to help. Low-pressure.",
    "birthday":         "Warm, personal note. Reference something specific if possible from the context. 2-3 sentences. Avoid corporate-speak.",
    "rsvp_alternative": "Decline the original time + propose 2-3 alternatives clearly. Easy for them to pick.",
    "translate_reply":  "Reply in the target language. Match tone of original. Localize idioms — don't word-for-word translate.",
    "scheduling_poll":  "Propose 3 specific time slots. Number them. Make replying effortless.",
}

# Per-audience tone hints.
_AUDIENCE_TONES = {
    "customer":      "Warm but professional. They're paying — show attentiveness, not deference.",
    "vendor":        "Polite, business-like. You're the customer — be respectful but firm on what's needed.",
    "employee":      "Direct, brief. Internal tone — skip the corporate niceties.",
    "internal_peer": "Casual, conversational. You work together every day.",
    "personal":      "Warm, personal. Like writing to a friend.",
}


def _build_system_prompt(req: DraftRequest) -> str:
    voice = load_voice_guide()
    intent_directive = _INTENT_DIRECTIVES.get(req.intent, "")
    tone_hint = _AUDIENCE_TONES.get(req.audience, "")
    parts = []
    if voice:
        parts.append(f"Brand voice guide:\n\n{voice}\n")
    parts.append(f"Audience: {req.audience}. {tone_hint}")
    parts.append(f"Intent: {req.intent}. {intent_directive}")
    if req.target_language:
        parts.append(f"Output language: {req.target_language}.")
    parts.append(
        "Output exactly two parts separated by a single line containing only "
        "'---'. First part is the subject line (one line, no quotes). Second "
        "part is the email body in plain text. No markdown headers, no signature."
    )
    return "\n\n".join(parts)


def _build_user_prompt(req: DraftRequest) -> str:
    parts = []
    if req.recipient_name:
        parts.append(f"Recipient: {req.recipient_name}")
    if req.sender_name:
        parts.append(f"Sender: {req.sender_name}")
    if req.subject_hint:
        parts.append(f"Reply to subject: {req.subject_hint}")
    if req.context:
        parts.append(f"Context:\n{req.context}")
    if req.facts:
        facts_lines = "\n".join(f"  - {k}: {v}" for k, v in req.facts.items())
        parts.append(f"Facts:\n{facts_lines}")
    if req.constraints:
        parts.append("Constraints:\n" + "\n".join(f"  - {c}" for c in req.constraints))
    parts.append("Compose the message.")
    return "\n\n".join(parts)


def _llm_compose(req: DraftRequest) -> DraftOutput:
    """LLM-backed compose. Falls back to template if LLM unavailable."""
    available, _ = llm.is_available()
    if not available:
        return _template_compose(req)
    try:
        result = llm.call_simple(
            prompt=_build_user_prompt(req),
            system=_build_system_prompt(req),
            max_tokens=600,
            temperature=0.0,
        )
    except Exception:
        return _template_compose(req)

    text = result.get("text", "").strip()
    # Parse subject + body from the "---" separator.
    if "\n---\n" in text:
        subject, body = text.split("\n---\n", 1)
    elif "---" in text:
        subject, body = text.split("---", 1)
    else:
        # Fallback: assume first line is subject, rest is body.
        first_nl = text.find("\n")
        if first_nl >= 0:
            subject = text[:first_nl]
            body = text[first_nl + 1:]
        else:
            subject = req.subject_hint or req.intent
            body = text

    subject = subject.strip().strip("'\"")
    body = body.strip()
    html = "<br>".join(body.splitlines()).replace("\n", "<br>")

    return DraftOutput(
        subject=subject, plain=body, html=html,
        intent=req.intent, audience=req.audience,
        voice_used=True, variant_seed=_seed(req),
        estimated_cost_usd=result.get("estimated_cost_usd"),
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def compose(req: DraftRequest) -> DraftOutput:
    """Compose a draft for the given request.

    Uses LLM when available, falls back to templates. Either way produces
    a DraftOutput with subject + plain + html + provenance.
    """
    if req.intent not in INTENTS:
        raise ValueError(f"Unknown intent: {req.intent!r}. Valid: {sorted(INTENTS)}")
    if req.audience not in AUDIENCES:
        raise ValueError(f"Unknown audience: {req.audience!r}. Valid: {sorted(AUDIENCES)}")
    return _llm_compose(req)


def compose_template_only(req: DraftRequest) -> DraftOutput:
    """Skip the LLM entirely — useful for tests and offline runs."""
    if req.intent not in INTENTS:
        raise ValueError(f"Unknown intent: {req.intent!r}. Valid: {sorted(INTENTS)}")
    return _template_compose(req)


# --------------------------------------------------------------------------- #
# Test override
# --------------------------------------------------------------------------- #


def _override_voice_path_for_tests(p: Path) -> None:
    global _BRAND_VOICE_PATH
    _BRAND_VOICE_PATH = p
