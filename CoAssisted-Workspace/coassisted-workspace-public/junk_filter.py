"""Heuristic junk-mail / automation detector.

Used by the contact enrichment pipeline to decide whether a message from a
sender represents real human communication (good for auto-creating + enriching
a contact) or an automated / marketing / transactional email (skip it).

Design
------
Two tiers of signals:

1. **Hard-fail** — any ONE of these classifies the email as junk.
   These are high-precision signals (almost no false positives).
     * Sender local-part matches `noreply`, `do-not-reply`, etc.
     * Sender domain matches common marketing/notification sub-domains.
     * `List-Unsubscribe` header present (the CAN-SPAM gold standard).
     * `Precedence: bulk|list` header.
     * `Auto-Submitted` header present and not `no`.

2. **Soft** — each category is +1; 2+ categories triggers junk.
   These would also match some legit emails in isolation.
     * Body boilerplate: "please do not reply", "this is an automated message".
     * Body opt-out: "unsubscribe", "manage subscription", "view in browser".
     * High link-to-text ratio (>=5 links and <200 words).
     * Transactional subject pattern ("receipt", "invoice", "order confirmation",
       "weekly digest", etc.).

Every decision returns a list of reasons for logging / debugging.

Public API
----------
    is_junk(sender, headers, body_text, subject) -> (bool, list[str])
"""

from __future__ import annotations

import re
from typing import Iterable


# --------------------------------------------------------------------------- #
# Pattern definitions
# --------------------------------------------------------------------------- #


# Sender local-part (before @): exact or prefix matches that indicate automation.
_SENDER_LOCAL_JUNK = {
    r"^no[-_.]?reply$",
    r"^do[-_.]?not[-_.]?reply$",
    r"^donotreply$",
    r"^noreply",            # `noreply_developer`, `noreply-something`
    r"^no-reply",
    r"^mailer[-_]?daemon$",
    r"^postmaster$",
    r"^bounce",
    r"^bounces?@",
    r"^notifications?$",
    r"^notify$",
    r"^alerts?$",
    r"^updates?$",
    r"^digest$",
    r"^newsletter$",
    r"^news@",
    r"^marketing$",
    r"^hello$",             # generic role address
    r"^info$",
    r"^support$",           # often legit but also often auto-ticketing; soft-flag at domain
    r"^admin$",
    r"^webmaster$",
    r"^security-?noreply",
    r"^auth@",
    r"^billing@",           # often transactional
    r"^receipts?@",
    r"^invoices?@",
    r"^orders?@",
    r"_noreply",
    r"-noreply",
    r"noreply_",
    r"noreply-",
    # Cloud / SaaS vendor marketing role accounts.
    r"^googlecloud$",
    r"^awscloud$",
    r"^aws$",
    r"^azure$",
    r"^microsoftazure$",
    r"^gcp$",
    r"^oraclecloud$",
    r"^ibmcloud$",
    r"^cloud$",
    r"^digitalocean$",
    r"^heroku$",
    r"^vercel$",
    r"^netlify$",
    r"^stripe$",
    r"^webinars?$",
    r"^announcements?$",
    r"^community$",
}

# Domain substrings that strongly suggest automation / marketing infrastructure.
# Matched with `in domain`, so partials like "notifications." catch a wide net.
_DOMAIN_JUNK_SUBSTRINGS = {
    "notifications.",
    "notification.",
    "notificationmail",
    "notificationemails",
    "emailnotifications",
    "accountprotection",
    "appcenter.",
    "developerrelations",
    "em.",                  # marketing
    "mkt.",                 # marketing
    "mail.",                # bulk-mail sending domain
    "email.",               # bulk-mail sending domain
    "bounces.",
    "bounce.",
    ".mailchimp",
    "sendgrid.net",
    "mandrillapp",
    "postmarkapp",
    "amazonses",
    "mailgun",
    "campaignmonitor",
}

# Body phrases that indicate the email is automated or is a marketing send.
_BODY_BOILERPLATE = (
    "this is an automated message",
    "this email was sent automatically",
    "please do not reply to this email",
    "do not reply to this email",
    "this email cannot receive replies",
    "this mailbox is not monitored",
    "this is a system-generated message",
)
_BODY_OPTOUT = (
    "unsubscribe",
    "manage subscription",
    "manage your subscription",
    "manage notifications",
    "manage preferences",
    "update your preferences",
    "update your notification preferences",
    "email preferences",
    "view in browser",
    "view as webpage",
    "view this email in your browser",
)

# Marketing / spam hype language. Word-boundary matched; need 2+ distinct hits
# for a soft signal, 4+ for a heavy signal (triggers junk on its own).
_BODY_SPAM_HYPE = (
    "100% more",
    "100% free",
    "100% satisfied",
    "additional income",
    "be your own boss",
    "best price",
    "big bucks",
    "billion",
    "cash bonus",
    "cents on the dollar",
    "consolidate debt",
    "double your cash",
    "double your income",
    "earn extra cash",
    "earn money",
    "eliminate bad credit",
    "extra cash",
    "extra income",
    "expect to earn",
    "fast cash",
    "financial freedom",
    "free access",
    "free consultation",
    "free gift",
    "free hosting",
    "free info",
    "free investment",
    "free membership",
    "free money",
    "free preview",
    "free quote",
    "free trial",
    "full refund",
    "get out of debt",
    "get paid",
    "giveaway",
    "guaranteed",
    "increase sales",
    "increase traffic",
    "incredible deal",
    "lower rates",
    "lowest price",
    "make money",
    "million dollars",
    "miracle",
    "money back",
    "once in a lifetime",
    "one time",
    "pennies a day",
    "potential earnings",
    "prize",
    "promise",
    "pure profit",
    "risk-free",
    "satisfaction guaranteed",
    "save big money",
    "save up to",
    "special promotion",
)
_SPAM_HYPE_RX = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _BODY_SPAM_HYPE) + r")\b",
    re.IGNORECASE,
)

# Subject patterns that almost always mean transactional / marketing.
_SUBJECT_TRANSACTIONAL = (
    r"\breceipt\b",
    r"\binvoice\b",
    r"\bstatement\b",
    r"\border (?:confirmation|receipt|update|shipped|placed)\b",
    r"\bpayment (?:received|confirmation|reminder|failed)\b",
    r"\byour (?:weekly|daily|monthly) (?:digest|summary|update|report)\b",
    r"\bverification code\b",
    r"\bsecurity alert\b",
    r"\bpassword (?:reset|changed)\b",
    r"\btwo[- ]factor\b",
    r"\baction required\b",
    r"\bwelcome to\b",
    r"\breset your password\b",
    r"\bverify your (?:email|account|identity)\b",
    r"\bconfirm your (?:email|account|subscription)\b",
    r"\bsign[- ]in (?:code|verification)\b",
    r"\b(?:your|new) (?:bill|statement|receipt) is ready\b",
    # "Thanks for upgrading / subscribing / purchasing" — classic product
    # transactional emails that masquerade as personal thank-yous.
    r"\bthanks? for (?:upgrading|subscribing|signing up|joining|purchasing|your (?:purchase|order|payment|subscription|interest|business))\b",
    r"\bthank you for (?:upgrading|subscribing|signing up|joining|purchasing|your (?:purchase|order|payment|subscription|interest|business))\b",
)


_LINK_RX = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_WORD_RX = re.compile(r"\b[A-Za-z][A-Za-z'\-]{2,}\b")


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _split_email(address: str) -> tuple[str, str]:
    a = (address or "").strip().lower()
    if "@" not in a:
        return a, ""
    local, _, domain = a.partition("@")
    return local, domain


def _sender_local_is_junk(local: str) -> str | None:
    """Return the matching pattern name, or None."""
    for pat in _SENDER_LOCAL_JUNK:
        if re.search(pat, local):
            return pat
    return None


def _sender_domain_is_junk(domain: str) -> str | None:
    for sub in _DOMAIN_JUNK_SUBSTRINGS:
        if sub in domain:
            return sub
    return None


def _header(headers: dict | None, name: str) -> str:
    """Case-insensitive header lookup returning the raw value or ''."""
    if not headers:
        return ""
    name_lc = name.lower()
    for k, v in headers.items():
        if (k or "").lower() == name_lc:
            return v or ""
    return ""


def _body_hits(body: str, phrases: Iterable[str]) -> list[str]:
    body_lc = (body or "").lower()
    return [p for p in phrases if p in body_lc]


def _subject_hit(subject: str) -> str | None:
    s_lc = (subject or "").lower()
    for pat in _SUBJECT_TRANSACTIONAL:
        if re.search(pat, s_lc):
            return pat
    return None


def _link_to_text_ratio_is_marketing(body: str) -> tuple[bool, int, int]:
    """True if body has many links and few words (typical marketing email).

    Returns (is_marketing_ratio, link_count, word_count).
    """
    links = len(_LINK_RX.findall(body or ""))
    words = len(_WORD_RX.findall(body or ""))
    return (links >= 5 and words < 200), links, words


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def is_junk(
    sender: str,
    headers: dict | None = None,
    body_text: str | None = None,
    subject: str | None = None,
) -> tuple[bool, list[str]]:
    """Decide whether a message from `sender` is junk / automated.

    Args:
      sender: the From address (bare email, e.g. 'noreply@apple.com').
      headers: optional dict of message headers, case-insensitive.
      body_text: optional plain-text body.
      subject: optional subject line.

    Returns:
      (is_junk, reasons) — the list explains which signals fired, for logging.
    """
    reasons: list[str] = []

    local, domain = _split_email(sender or "")

    # --- Hard-fail signals --- #

    if m := _sender_local_is_junk(local):
        reasons.append(f"sender_local:{m}")
    if m := _sender_domain_is_junk(domain):
        reasons.append(f"sender_domain:{m}")

    if _header(headers, "List-Unsubscribe"):
        reasons.append("header:list_unsubscribe")

    precedence = _header(headers, "Precedence").strip().lower()
    if precedence in ("bulk", "list", "junk"):
        reasons.append(f"header:precedence={precedence}")

    auto_sub = _header(headers, "Auto-Submitted").strip().lower()
    if auto_sub and auto_sub != "no":
        reasons.append(f"header:auto_submitted={auto_sub}")

    x_auto = _header(headers, "X-Auto-Response-Suppress")
    if x_auto:
        reasons.append("header:x_auto_response_suppress")

    # Any hard-fail → junk.
    hard_prefixes = ("sender_local:", "sender_domain:", "header:")
    if any(any(r.startswith(p) for p in hard_prefixes) for r in reasons):
        return True, reasons

    # --- Soft signals (need 2+ categories) --- #

    soft_categories: list[str] = []

    bp = _body_hits(body_text or "", _BODY_BOILERPLATE)
    if bp:
        soft_categories.append("body_boilerplate")
        reasons.append(f"body_boilerplate:{bp[:2]}")

    opt = _body_hits(body_text or "", _BODY_OPTOUT)
    if opt:
        soft_categories.append("body_optout")
        reasons.append(f"body_optout:{opt[:2]}")
        # 2+ distinct opt-out phrases (e.g. "unsubscribe" + "view in browser")
        # is a strong standalone marketing signal — count as an extra category.
        if len(opt) >= 2:
            soft_categories.append("body_optout_multi")

    is_marketing_ratio, links, words = _link_to_text_ratio_is_marketing(body_text or "")
    if is_marketing_ratio:
        soft_categories.append("link_ratio")
        reasons.append(f"link_ratio:links={links},words={words}")

    if subject and (m := _subject_hit(subject)):
        soft_categories.append("subject_transactional")
        reasons.append(f"subject:{m}")

    # Spam / marketing hype language. Count DISTINCT phrase matches (not raw
    # occurrences), so a single phrase repeated doesn't bias the score.
    body_lc = (body_text or "").lower()
    hype_hits_raw = _SPAM_HYPE_RX.findall(body_lc)
    hype_distinct = sorted({h.lower() for h in hype_hits_raw})
    if len(hype_distinct) >= 2:
        soft_categories.append("body_spam_hype")
        reasons.append(
            f"body_spam_hype:distinct={len(hype_distinct)},examples={hype_distinct[:3]}"
        )
        # 4+ distinct hype phrases is strong enough to classify as junk on its own.
        if len(hype_distinct) >= 4:
            soft_categories.append("body_spam_hype_heavy")

    if len(soft_categories) >= 2:
        return True, reasons

    return False, reasons
