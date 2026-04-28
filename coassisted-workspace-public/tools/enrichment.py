"""Contact enrichment tools — parse email signatures from received mail.

Two tools:
  * workflow_enrich_contact_from_inbox — one contact at a time.
  * workflow_enrich_contacts_from_recent_mail — sweep recent inbox (daily-job
    friendly: default 1-day window), match each From address to a saved
    contact, parse its signature, and fill in phone / website / title / etc.

Design notes
------------
* Fields we fill: organization, title (conservative), phones (E.164), URLs
  (first is website, others go into custom fields: linkedin, twitter, etc.),
  first_name / last_name when missing.
* Overwrite policy is controlled per-call via `overwrite` (default True).
  Rules-populated fields are safe to overwrite because enrichment data is
  more recent and specific.
* Signature detection is heuristic. We strip quoted reply content, look for
  the RFC-2646 `-- ` marker, common sign-offs, or a trailing block of short
  lines, then regex-extract the fields.
* No new Python deps. Phone normalization is handled with an inline helper.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import config as app_config  # noqa: F401 — reserved for future use
import gservices
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log
from junk_filter import is_junk


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class EnrichContactInput(BaseModel):
    """Enrich one saved contact from their recent inbound mail."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: Optional[str] = Field(
        default=None,
        description="People API resource_name — e.g. 'people/c123'. Provide this OR email.",
    )
    email: Optional[str] = Field(
        default=None,
        description="Contact email. Used to search for a matching saved contact if resource_name is omitted.",
    )
    days: int = Field(
        default=180,
        ge=1,
        le=3650,
        description="How far back to scan inbox for a message from this contact. Default 180.",
    )
    overwrite: bool = Field(
        default=True,
        description="If True, overwrite existing contact fields with fresh signature data. "
                    "If False, only fill blanks.",
    )
    conservative_titles: bool = Field(
        default=True,
        description="If True, only extract a title when it contains a known title keyword. "
                    "If False, guess more aggressively.",
    )
    dry_run: Optional[bool] = Field(default=None)


class EnrichFromRecentMailInput(BaseModel):
    """Walk recent received mail and enrich every contact who appears as a sender."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days: int = Field(
        default=1,
        ge=1,
        le=3650,
        description="How many days of inbox mail to scan. Default 1 (daily-job friendly). "
                    "Pass a larger number for a deep historical sweep.",
    )
    limit_messages_scanned: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Max messages to inspect from the window (newest first).",
    )
    overwrite: bool = Field(
        default=True,
        description="If True, overwrite existing contact fields with fresh signature data. "
                    "If False, only fill blanks.",
    )
    conservative_titles: bool = Field(default=True)
    only_existing_contacts: bool = Field(
        default=True,
        description="If True (default), only enrich senders who already exist as saved contacts. "
                    "If False, also auto-create new contacts for unknown senders (like "
                    "workflow_create_contacts_from_sent_mail but for your inbox).",
    )
    dry_run: Optional[bool] = Field(default=None)


# --------------------------------------------------------------------------- #
# Signature parsing
# --------------------------------------------------------------------------- #


# Phrases that kick off quoted/replied content. Anything from here down is cut.
_QUOTE_MARKERS = [
    re.compile(r"^On\s.+?\swrote:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^-{3,}\s*Original Message\s*-{3,}\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),  # Outlook horizontal divider
    re.compile(r"^From:\s.+$", re.IGNORECASE | re.MULTILINE),  # Outlook reply header start
    re.compile(r"^Sent from my .+$", re.IGNORECASE | re.MULTILINE),  # mobile client sig — strip
    re.compile(r"^Get Outlook for .+$", re.IGNORECASE | re.MULTILINE),
]

# Common sign-off openers — signature likely starts at the next non-empty line.
_SIGNOFF_RX = re.compile(
    r"^(?:best(?:\s+regards)?|regards|thanks(?:\s+so\s+much)?|thank\s+you|"
    r"cheers|sincerely|warmly|kindly|talk\s+soon|speak\s+soon|"
    r"all\s+the\s+best|yours(?:\s+truly)?)[,.!]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_RFC_SIG_MARKER = re.compile(r"^--\s?$", re.MULTILINE)

_URL_RX = re.compile(
    r"\b(?:https?://|www\.)[\w\-._~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)
_EMAIL_RX = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Loose phone pattern — at least 7 digits with common separators.
_PHONE_RX = re.compile(
    r"(?:(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{3,4}(?:[\s.\-]?\d{1,4})?)"
)

# Very conservative title keyword list — if a line has one of these, it's probably a title.
_TITLE_KEYWORDS = {
    "ceo", "cfo", "coo", "cto", "cmo", "cio", "cso", "cpo", "chro",
    "president", "vp", "svp", "evp", "avp",
    "director", "manager", "lead", "head", "chief",
    "principal", "senior", "junior", "associate", "staff",
    "engineer", "developer", "designer", "analyst", "consultant", "architect",
    "partner", "advisor", "founder", "co-founder", "cofounder", "owner",
    "specialist", "coordinator", "administrator", "officer", "representative",
    "executive", "recruiter", "account", "sales", "marketing", "product",
    "operations", "strategy", "finance", "engineering",
}

# Tokens that make a line NOT a title.
_TITLE_EXCLUDE_CHARS = set("@©®™")


def _extract_plaintext_body(payload: dict) -> str:
    """Walk a Gmail message payload and concatenate all text/plain parts.

    Falls back to a crude HTML-to-text strip if no plain text is available.
    """
    plain = _walk_plain(payload)
    if plain.strip():
        return plain
    html = _walk_html(payload)
    if html:
        # Crude strip: remove tags, decode common entities, collapse whitespace.
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = (text.replace("&nbsp;", " ")
                    .replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&quot;", '"')
                    .replace("&#39;", "'"))
        return text
    return ""


def _walk_plain(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        try:
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return ""
    out: list[str] = []
    for part in payload.get("parts", []) or []:
        chunk = _walk_plain(part)
        if chunk:
            out.append(chunk)
    return "\n\n".join(out)


def _walk_html(payload: dict) -> str:
    if payload.get("mimeType") == "text/html" and "data" in payload.get("body", {}):
        try:
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return ""
    out: list[str] = []
    for part in payload.get("parts", []) or []:
        chunk = _walk_html(part)
        if chunk:
            out.append(chunk)
    return "\n".join(out)


def _strip_quoted(body: str) -> str:
    """Remove everything from the first quote marker downward."""
    if not body:
        return ""
    earliest = len(body)
    for rx in _QUOTE_MARKERS:
        m = rx.search(body)
        if m and m.start() < earliest:
            earliest = m.start()
    body = body[:earliest]
    # Strip trailing `>`-quoted blocks line-by-line.
    lines = body.splitlines()
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    return "\n".join(lines).rstrip()


def _isolate_signature(body: str) -> str:
    """Return just the signature block of a de-quoted message body.

    Strategy (first match wins):
      1. RFC-2646 `-- \\n` marker.
      2. Text after the last sign-off phrase ('Best,', 'Thanks,', etc.).
      3. The trailing block of short, non-prose lines.
    """
    if not body:
        return ""

    # 1. RFC marker.
    m = _RFC_SIG_MARKER.search(body)
    if m:
        return body[m.end():].strip()

    # 2. Last sign-off phrase.
    matches = list(_SIGNOFF_RX.finditer(body))
    if matches:
        last = matches[-1]
        candidate = body[last.end():].strip()
        if candidate and len(candidate.splitlines()) <= 12:
            return candidate

    # 3. Trailing block of short lines (< 60 chars each, no sentence punctuation
    #    internal). Walk backward; stop when we hit a long or prose-y line.
    lines = [ln.rstrip() for ln in body.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    tail: list[str] = []
    for ln in reversed(lines):
        stripped = ln.strip()
        if not stripped:
            if tail:
                # Blank line acts as a soft delimiter.
                break
            continue
        if len(stripped) > 80:
            break
        # Sentence-looking line (long-ish with period mid-line) → probably prose.
        if len(stripped) > 40 and re.search(r"[a-z]\.\s+[A-Z]", stripped):
            break
        tail.insert(0, stripped)
        if len(tail) >= 10:
            break
    return "\n".join(tail).strip()


def _extract_host(url: str) -> str:
    """Pull the bare host out of a URL. '' if not parseable."""
    if not url:
        return ""
    # Strip protocol.
    s = url.split("://", 1)[-1]
    # Strip everything from the first / or ? or #.
    for ch in ("/", "?", "#"):
        if ch in s:
            s = s.split(ch, 1)[0]
    # Strip port.
    if ":" in s:
        s = s.split(":", 1)[0]
    return s.strip().lower()


def _looks_like_real_phone(raw: str) -> bool:
    """True if `raw` has the shape of a real phone number.

    Real phone strings have at least 2 separators between 3+ digit groups
    (country/area/local). Rejects bare digit strings and timestamps.
    """
    if not raw:
        return False
    groups = re.findall(r"\d+", raw)
    if len(groups) < 3:
        return False
    total_digits = sum(len(g) for g in groups)
    if total_digits < 7 or total_digits > 15:
        return False
    return True


def _normalize_phone(raw: str, default_region: str = "US") -> str | None:
    """Normalize to E.164 ('+15551234567'). Returns None if we can't confidently.

    Pragmatic rules (no external deps):
      * Strip all non-digit chars except a leading '+'.
      * If starts with '+', keep as-is (validate length 8..15).
      * If 11 digits and starts with '1' (US/Canada), prepend '+'.
      * If 10 digits and default_region='US', prepend '+1'.
      * Otherwise None — unconfident.
    """
    if not raw:
        return None
    s = raw.strip()
    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if has_plus:
        if 8 <= len(digits) <= 15:
            return "+" + digits
        return None
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10 and default_region == "US":
        return "+1" + digits
    if len(digits) == 7:
        # No area code — skip (would be a landline fragment).
        return None
    if 8 <= len(digits) <= 15:
        return "+" + digits
    return None


def _looks_like_title(line: str, conservative: bool = True) -> bool:
    line = line.strip()
    if not line or len(line) > 80 or len(line) < 3:
        return False
    if any(ch in line for ch in _TITLE_EXCLUDE_CHARS):
        return False
    if _URL_RX.search(line) or _EMAIL_RX.search(line):
        return False
    # A line that is mostly digits is a phone number.
    digit_ratio = sum(ch.isdigit() for ch in line) / max(1, len(line))
    if digit_ratio > 0.3:
        return False
    if conservative:
        low = line.lower()
        tokens = re.split(r"[\s,/|&]+", low)
        if not any(t in _TITLE_KEYWORDS for t in tokens if t):
            return False
    # Reject sentence-looking strings.
    if line.endswith("."):
        return False
    return True


def parse_signature_fields(
    body: str,
    known_email: str,
    *,
    conservative_titles: bool = True,
    enhance_with_llm: bool = False,
    force_llm: bool = False,
) -> dict[str, Any]:
    """Parse a message body into a dict of enrichment fields.

    Args:
        body: full plaintext body to parse.
        known_email: the contact's primary email (used to dedupe in alt_emails).
        conservative_titles: regex-only flag — controls how aggressive the title
            heuristic is.
        enhance_with_llm: if True AND the regex pass missed `title` or
            `organization`, call Claude to fill the gaps. Cost ~$0.001 per call.
        force_llm: if True, always call Claude in addition to regex (LLM
            answers fill gaps, regex wins where both have answers). More
            expensive than enhance_with_llm; meant for power users.

    Returns keys (any may be absent):
      first_name, last_name, organization, title,
      phones (list[str] in E.164 when normalizable),
      urls (list[str]), linkedin, twitter, facebook,
      alt_emails (list[str]),
      raw_signature (the block we parsed, for debugging/logs).
    """
    clean = _strip_quoted(body or "")
    sig = _isolate_signature(clean)
    out: dict[str, Any] = {"raw_signature": sig}
    if not sig:
        return out

    lines = [ln.strip() for ln in sig.splitlines() if ln.strip()]

    # URLs / social handles.
    urls: list[str] = []
    socials: dict[str, str] = {}
    # Also catch protocol-less social URLs common in signatures (e.g. 'linkedin.com/in/jane').
    bare_social_rx = re.compile(
        r"\b(?:linkedin\.com|twitter\.com|x\.com|facebook\.com|instagram\.com)/[\w\-._~/?#&=%]+",
        re.IGNORECASE,
    )
    for ln in lines:
        candidates: list[str] = []
        for m in _URL_RX.finditer(ln):
            candidates.append(m.group(0).rstrip(".,;:)"))
        for m in bare_social_rx.finditer(ln):
            bare = m.group(0).rstrip(".,;:)")
            if not any(bare.lower() in c.lower() for c in candidates):
                candidates.append("https://" + bare)
        for u in candidates:
            if u.lower().startswith("www."):
                u = "https://" + u
            host = _extract_host(u).lower()
            if host == "linkedin.com" or host.endswith(".linkedin.com"):
                if "linkedin" not in socials:
                    socials["linkedin"] = u
            elif host in ("twitter.com", "x.com") or host.endswith(
                (".twitter.com", ".x.com")
            ):
                if "twitter" not in socials:
                    socials["twitter"] = u
            elif host == "facebook.com" or host.endswith(".facebook.com"):
                if "facebook" not in socials:
                    socials["facebook"] = u
            elif host == "instagram.com" or host.endswith(".instagram.com"):
                if "instagram" not in socials:
                    socials["instagram"] = u
            elif u not in urls:
                urls.append(u)

    # Prefer URLs whose host matches the contact's email domain as the "website".
    if urls and known_email and "@" in known_email:
        sender_domain = known_email.split("@", 1)[1].lower()
        domain_matches = [
            u for u in urls
            if _extract_host(u).lower().endswith(sender_domain)
        ]
        others = [u for u in urls if u not in domain_matches]
        urls = domain_matches + others
    if urls:
        out["urls"] = urls
    out.update(socials)

    # Emails (excluding the known one — that's us already).
    alt_emails: list[str] = []
    for ln in lines:
        for m in _EMAIL_RX.finditer(ln):
            addr = m.group(0)
            if addr.lower() != (known_email or "").lower() and addr not in alt_emails:
                alt_emails.append(addr)
    if alt_emails:
        out["alt_emails"] = alt_emails

    # Phones.
    phones: list[str] = []
    for ln in lines:
        # Skip lines that look like URLs or emails — their digits aren't phone.
        if _URL_RX.search(ln) or _EMAIL_RX.search(ln):
            continue
        for m in _PHONE_RX.finditer(ln):
            raw_match = m.group(0)
            # Require the raw match to look like a real phone (separators between
            # digit groups, not just a bare digit string / timestamp / ID).
            if not _looks_like_real_phone(raw_match):
                continue
            norm = _normalize_phone(raw_match)
            if norm and norm not in phones:
                phones.append(norm)
    if phones:
        out["phones"] = phones

    # Name: the first "clean" line is usually the person's name.
    # Clean = no URL, no email, no phone digits > 30%, 2-4 words of letters.
    name_candidate = None
    for ln in lines[:5]:
        if _URL_RX.search(ln) or _EMAIL_RX.search(ln):
            continue
        if sum(ch.isdigit() for ch in ln) / max(1, len(ln)) > 0.2:
            continue
        words = ln.split()
        if not (1 <= len(words) <= 5):
            continue
        if not all(re.match(r"^[A-Za-z][A-Za-z\-\'\.]*$", w) for w in words):
            continue
        if any(w.lower() in _TITLE_KEYWORDS for w in words):
            continue
        name_candidate = ln
        break
    if name_candidate:
        parts = name_candidate.split()
        out["first_name"] = parts[0]
        if len(parts) > 1:
            out["last_name"] = " ".join(parts[1:])

    # Title: a non-name, non-URL, non-phone line that passes the title heuristic.
    for ln in lines:
        if name_candidate and ln == name_candidate:
            continue
        if _looks_like_title(ln, conservative=conservative_titles):
            out["title"] = ln
            break

    # Organization: try to detect a line after the title, or infer from email domain.
    # Simple pass: line that's Title Case, short, and not a title/name already parsed.
    for idx, ln in enumerate(lines[:8]):
        if ln in (name_candidate, out.get("title")):
            continue
        if _URL_RX.search(ln) or _EMAIL_RX.search(ln):
            continue
        if any(ch.isdigit() for ch in ln):
            continue
        if 3 <= len(ln) <= 60 and ln[0:1].isupper() and len(ln.split()) <= 6:
            # Filter out obvious non-orgs (sign-off phrases etc.).
            if _SIGNOFF_RX.match(ln):
                continue
            out["organization"] = ln
            break

    # Optional LLM enhancement.
    needs_llm = force_llm or (
        enhance_with_llm and (not out.get("title") or not out.get("organization"))
    )
    if needs_llm and sig:
        try:
            llm_fields = _llm_parse_signature(sig, known_email)
            _merge_llm_into_out(out, llm_fields)
        except Exception as e:
            log.warning(
                "parse_signature_fields: LLM enhancement skipped (%s)", e
            )

    return out


def _llm_parse_signature(sig: str, known_email: str) -> dict[str, Any]:
    """Send a signature block to Claude and return structured fields.

    Returns {} if the LLM is unavailable or returns malformed JSON. Callers
    are expected to use this only as an enhancement; never fail enrichment
    just because the LLM call didn't pan out.
    """
    import llm as _llm  # local import — keeps top-level imports unaware of LLM
    ok, reason = _llm.is_available()
    if not ok:
        log.info("LLM signature parse skipped: %s", reason)
        return {}

    prompt = (
        "You're parsing an email signature block. Return JSON ONLY (no commentary, "
        "no code fences). Include only fields you're confident about — omit any you "
        "can't determine.\n\n"
        "Schema (all keys optional):\n"
        '  - "first_name": string\n'
        '  - "last_name": string\n'
        '  - "title": job title string\n'
        '  - "organization": company name string\n'
        '  - "phones": array of phone strings (E.164 if you can normalize, e.g. "+15551234567")\n'
        '  - "urls": array of website URLs (NOT social URLs)\n'
        '  - "linkedin": LinkedIn profile URL\n'
        '  - "twitter": Twitter or X profile URL\n'
        f'  - "alt_emails": array of alternative emails, EXCLUDING {known_email}\n\n'
        "Be conservative. If a value is uncertain, omit the key entirely.\n\n"
        "Signature:\n"
        f"{sig[:2000]}\n\n"
        "JSON:"
    )

    result = _llm.call_simple(
        prompt,
        model="claude-haiku-4-5",
        max_tokens=600,
        temperature=0.0,
    )
    text = (result.get("text") or "").strip()
    # Strip code fences if Claude added them.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning(
            "LLM signature parse returned non-JSON: %s — preview: %r",
            e, text[:160],
        )
        return {}

    if not isinstance(parsed, dict):
        return {}

    log.info(
        "LLM signature parse done — %d in / %d out tokens, ~$%s",
        result.get("input_tokens", 0),
        result.get("output_tokens", 0),
        result.get("estimated_cost_usd"),
    )
    return parsed


def _signature_llm_flags() -> tuple[bool, bool]:
    """Read config.signature_parser_mode and translate to (enhance, force) flags.

    Modes:
        "regex"           → (False, False) — regex only.
        "regex_then_llm"  → (True, False)  — call LLM only if title/org missing.
        "llm"             → (True, True)   — always call LLM and merge with regex.

    Defaults to "regex" if config is missing or has an unknown value.
    """
    mode = (app_config.get("signature_parser_mode") or "regex").lower().strip()
    if mode == "regex_then_llm":
        return True, False
    if mode == "llm":
        return True, True
    return False, False


def _merge_llm_into_out(out: dict, llm_fields: dict) -> None:
    """Merge LLM-derived fields into the regex-derived out dict.

    Rules:
      - Scalars (first_name, last_name, title, organization, linkedin,
        twitter, facebook, instagram): LLM fills BLANKS only. Regex wins
        where it found something.
      - Lists (phones, urls, alt_emails): append unique items the regex
        didn't already find.
      - Garbage names from the regex pass (e.g. "the", "all") get tossed
        and replaced by LLM if available.
    """
    SCALAR_KEYS = (
        "first_name", "last_name", "title", "organization",
        "linkedin", "twitter", "facebook", "instagram",
    )
    LIST_KEYS = ("phones", "urls", "alt_emails")

    bad_name_words = {"the", "all", "rights", "no", "do", "you", "we", "this"}
    first_lc = (out.get("first_name") or "").lower()
    if first_lc in bad_name_words:
        out.pop("first_name", None)
        # If first_name was garbage, last_name without it is also suspect.
        out.pop("last_name", None)
    elif (out.get("last_name") or "").lower() in bad_name_words:
        out.pop("last_name", None)

    for key in SCALAR_KEYS:
        if not out.get(key) and llm_fields.get(key):
            val = llm_fields[key]
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()

    for key in LIST_KEYS:
        existing = list(out.get(key) or [])
        seen_lower = {str(x).lower() for x in existing}
        for item in llm_fields.get(key) or []:
            if not isinstance(item, str):
                continue
            v = item.strip()
            if v and v.lower() not in seen_lower:
                existing.append(v)
                seen_lower.add(v.lower())
        if existing:
            out[key] = existing


# --------------------------------------------------------------------------- #
# Gmail + People API glue
# --------------------------------------------------------------------------- #


def _gmail():
    return gservices.gmail()


def _people():
    return gservices.people()


def _list_all_saved_contacts_by_email() -> dict[str, dict]:
    """Return a map {email_lower: person_record} for every saved contact.

    One paginated connections() sweep. Avoids People API `searchContacts`
    indexing lag for newly-created contacts.
    """
    svc = _people()
    out: dict[str, dict] = {}
    page_token = None
    while True:
        kwargs: dict[str, Any] = {
            "resourceName": "people/me",
            "personFields": (
                "names,emailAddresses,organizations,phoneNumbers,urls,"
                "userDefined,biographies,metadata"
            ),
            "pageSize": 1000,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.people().connections().list(**kwargs).execute()
        for p in resp.get("connections", []) or []:
            for ea in p.get("emailAddresses") or []:
                v = (ea.get("value") or "").lower()
                if v:
                    out.setdefault(v, p)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _find_contact_by_email(
    email: str, preloaded: dict[str, dict] | None = None
) -> dict | None:
    """Return the People API person record for a saved contact matching `email`, or None.

    Prefers the preloaded map for freshness + speed. Falls back to a
    connections() sweep when no map is provided.
    """
    if not email:
        return None
    key = email.lower()
    if preloaded is not None:
        return preloaded.get(key)
    try:
        return _list_all_saved_contacts_by_email().get(key)
    except Exception as e:
        log.warning("_find_contact_by_email %s failed: %s", email, e)
        return None


def _fetch_latest_message_from(email: str, days: int) -> dict | None:
    """Return the newest Gmail message from `email` in the last `days`, or None."""
    gmail = _gmail()
    q = f"from:{email} newer_than:{days}d"
    try:
        resp = gmail.users().messages().list(userId="me", q=q, maxResults=1).execute()
        msgs = resp.get("messages", []) or []
        if not msgs:
            return None
        return gmail.users().messages().get(
            userId="me", id=msgs[0]["id"], format="full"
        ).execute()
    except Exception as e:
        log.warning("fetch latest from %s failed: %s", email, e)
        return None


def _apply_enrichment(
    person: dict,
    parsed: dict,
    *,
    overwrite: bool,
) -> dict:
    """Build an updateContact body that applies parsed fields to `person`.

    Returns {"changed": list[str], "body": dict, "mask": str} or
    {"changed": [], "body": None, "mask": ""} if no update is needed.
    """
    existing_names = person.get("names") or []
    existing_orgs = person.get("organizations") or []
    existing_phones = person.get("phoneNumbers") or []
    existing_urls = person.get("urls") or []
    existing_custom = person.get("userDefined") or []

    changed: list[str] = []
    body: dict = {"etag": person["etag"]}

    # --- Names ---
    new_first = parsed.get("first_name")
    new_last = parsed.get("last_name")
    cur_first = existing_names[0].get("givenName") if existing_names else ""
    cur_last = existing_names[0].get("familyName") if existing_names else ""
    want_first = new_first if new_first and (overwrite or not cur_first) else cur_first
    want_last = new_last if new_last and (overwrite or not cur_last) else cur_last
    if (want_first or "") != (cur_first or "") or (want_last or "") != (cur_last or ""):
        name_entry: dict = {}
        if want_first:
            name_entry["givenName"] = want_first
        if want_last:
            name_entry["familyName"] = want_last
        body["names"] = [name_entry] if name_entry else []
        changed.append("names")

    # --- Organization / title ---
    new_org = parsed.get("organization")
    new_title = parsed.get("title")
    cur_org = existing_orgs[0].get("name") if existing_orgs else ""
    cur_title = existing_orgs[0].get("title") if existing_orgs else ""
    want_org = new_org if new_org and (overwrite or not cur_org) else cur_org
    want_title = new_title if new_title and (overwrite or not cur_title) else cur_title
    if (want_org or "") != (cur_org or "") or (want_title or "") != (cur_title or ""):
        org_entry: dict = {}
        if want_org:
            org_entry["name"] = want_org
        if want_title:
            org_entry["title"] = want_title
        body["organizations"] = [org_entry] if org_entry else []
        changed.append("organizations")

    # --- Phones ---
    new_phones = parsed.get("phones") or []
    cur_phone_values = [(p.get("value") or "") for p in existing_phones]
    if new_phones:
        if overwrite or not existing_phones:
            # Replace with the normalized set, preserving existing if no new.
            merged = list(new_phones)
        else:
            merged = cur_phone_values + [p for p in new_phones if p not in cur_phone_values]
        if merged != cur_phone_values:
            body["phoneNumbers"] = [
                {"value": p, "type": "work" if i == 0 else "mobile"}
                for i, p in enumerate(merged)
            ]
            changed.append("phoneNumbers")

    # --- URLs (website + any extras that aren't socials) ---
    new_urls = parsed.get("urls") or []
    cur_url_values = [(u.get("value") or "") for u in existing_urls]
    if new_urls:
        if overwrite or not existing_urls:
            merged_urls = list(new_urls)
        else:
            merged_urls = cur_url_values + [u for u in new_urls if u not in cur_url_values]
        if merged_urls != cur_url_values:
            body["urls"] = [
                {"value": u, "type": "work" if i == 0 else "other"}
                for i, u in enumerate(merged_urls)
            ]
            changed.append("urls")

    # --- Custom fields: socials, alt_emails ---
    # People API userDefined is a flat key/value list; merge by key.
    cur_by_key = {c.get("key"): c.get("value") for c in existing_custom}
    added_custom = dict(cur_by_key)
    custom_changed = False
    for k in ("linkedin", "twitter", "facebook", "instagram"):
        v = parsed.get(k)
        if v and (overwrite or not cur_by_key.get(k)):
            if added_custom.get(k) != v:
                added_custom[k] = v
                custom_changed = True
    alt = parsed.get("alt_emails") or []
    if alt:
        alt_str = ", ".join(alt[:5])
        key = "alt_emails"
        if overwrite or not cur_by_key.get(key):
            if added_custom.get(key) != alt_str:
                added_custom[key] = alt_str
                custom_changed = True
    if custom_changed:
        body["userDefined"] = [
            {"key": k, "value": str(v)} for k, v in added_custom.items() if v
        ]
        changed.append("userDefined")

    if not changed:
        return {"changed": [], "body": None, "mask": ""}
    mask = ",".join(changed)
    return {"changed": changed, "body": body, "mask": mask}


def _message_headers(msg: dict) -> dict[str, str]:
    """Flatten a Gmail payload's headers to a dict (case-preserving)."""
    return {
        h.get("name", ""): h.get("value", "")
        for h in (msg.get("payload", {}) or {}).get("headers", []) or []
    }


def _enrich_one(
    email: str,
    days: int,
    *,
    overwrite: bool,
    conservative_titles: bool,
    dry_run: bool,
    preloaded_message: dict | None = None,
    preloaded_contacts: dict[str, dict] | None = None,
) -> dict:
    """Core per-contact enrichment. Returns a status dict."""
    person = _find_contact_by_email(email, preloaded=preloaded_contacts)
    if not person:
        return {"email": email, "status": "skipped_no_saved_contact"}

    msg = preloaded_message or _fetch_latest_message_from(email, days)
    if not msg:
        return {
            "email": email,
            "resource_name": person.get("resourceName"),
            "status": "skipped_no_recent_mail",
        }

    body_text = _extract_plaintext_body(msg.get("payload") or {})

    # Junk filter — don't pollute real contacts with garbage parsed from a
    # marketing or transactional email they might have received.
    headers_map = _message_headers(msg)
    subject = headers_map.get("Subject", "") or headers_map.get("subject", "")
    junk, reasons = is_junk(email, headers_map, body_text, subject)
    if junk:
        log.info("enrich_one: skipping junk message for %s: %s", email, reasons)
        return {
            "email": email,
            "resource_name": person.get("resourceName"),
            "status": "skipped_junk_message",
            "junk_reasons": reasons,
        }

    enhance, force = _signature_llm_flags()
    parsed = parse_signature_fields(
        body_text, known_email=email,
        conservative_titles=conservative_titles,
        enhance_with_llm=enhance, force_llm=force,
    )
    plan = _apply_enrichment(person, parsed, overwrite=overwrite)
    if not plan["changed"]:
        return {
            "email": email,
            "resource_name": person.get("resourceName"),
            "status": "no_changes_needed",
            "parsed_fields": {k: v for k, v in parsed.items() if k != "raw_signature"},
        }

    if dry_run:
        return {
            "email": email,
            "resource_name": person.get("resourceName"),
            "status": "dry_run",
            "would_change": plan["changed"],
            "parsed_fields": {k: v for k, v in parsed.items() if k != "raw_signature"},
        }

    try:
        _people().people().updateContact(
            resourceName=person["resourceName"],
            updatePersonFields=plan["mask"],
            body=plan["body"],
        ).execute()
    except Exception as e:
        log.error("enrichment updateContact failed for %s: %s", email, e)
        return {
            "email": email,
            "resource_name": person.get("resourceName"),
            "status": "failed",
            "error": str(e),
        }

    log.info(
        "enriched %s (%s): %s", person.get("resourceName"), email, plan["changed"]
    )
    return {
        "email": email,
        "resource_name": person.get("resourceName"),
        "status": "updated",
        "fields_changed": plan["changed"],
        "parsed_fields": {k: v for k, v in parsed.items() if k != "raw_signature"},
    }


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:

    @mcp.tool(
        name="workflow_enrich_contact_from_inbox",
        annotations={
            "title": "Enrich one contact from their signature in inbound mail",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_enrich_contact_from_inbox(params: EnrichContactInput) -> str:
        """Enrich a single saved contact by parsing their most recent inbound email signature.

        Looks up the contact (by resource_name or by email search), finds their
        newest message in `in:<email> newer_than:<days>d`, strips quoted
        content, isolates the signature block, and fills in name, title,
        organization, phones (E.164), website, and social URLs.

        Behavior:
          * `overwrite=True` (default): replace existing fields with fresh data.
          * `overwrite=False`: fill blanks only.
          * Supports `dry_run` — returns the planned update without writing.
        """
        try:
            email = params.email
            if not email and params.resource_name:
                # Fetch the contact to learn their primary email.
                person = _people().people().get(
                    resourceName=params.resource_name,
                    personFields="emailAddresses,names,metadata",
                ).execute()
                addrs = person.get("emailAddresses") or []
                if not addrs:
                    return "Error: contact has no email addresses on file."
                email = addrs[0].get("value")
            if not email:
                return "Error: provide either resource_name or email."

            result = _enrich_one(
                email=email,
                days=params.days,
                overwrite=params.overwrite,
                conservative_titles=params.conservative_titles,
                dry_run=bool(is_dry_run(params.dry_run)),
            )

            if is_dry_run(params.dry_run) and result.get("status") == "dry_run":
                return dry_run_preview(
                    "workflow_enrich_contact_from_inbox", result
                )
            return json.dumps(result, indent=2)
        except Exception as e:
            log.error("workflow_enrich_contact_from_inbox failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_enrich_contacts_from_recent_mail",
        annotations={
            "title": "Sweep recent inbound mail and enrich every sender who's a saved contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_enrich_contacts_from_recent_mail(
        params: EnrichFromRecentMailInput,
    ) -> str:
        """Walk `in:inbox newer_than:<days>d`, group by sender, and enrich matching contacts.

        Daily-cron friendly with `days=1` (default). For deep historical
        backfills, pass a larger `days`.

        Workflow:
          1. List recent inbox messages (capped by `limit_messages_scanned`).
          2. Fetch each message's metadata/body just once.
          3. Group by From address, keeping the newest message per sender.
          4. For each sender that matches a saved contact, parse the signature
             and apply the enrichment.

        `only_existing_contacts=False` also auto-creates new contacts for
        unknown senders (same behavior as create-from-sent-mail, but for your
        inbox side).
        """
        try:
            gmail = _gmail()
            query = f"in:inbox newer_than:{params.days}d"

            # 0. Preload all saved contacts by email for fast, fresh lookups.
            preloaded_contacts = _list_all_saved_contacts_by_email()

            # Build the self-exclusion set: user's primary address + every
            # configured send-as alias. Used to skip auto-creating "yourself"
            # as a saved contact when test emails or self-sent mail show up
            # in the inbox.
            self_addrs: set[str] = set()
            try:
                prof = gmail.users().getProfile(userId="me").execute()
                me_addr = (prof.get("emailAddress") or "").lower()
                if me_addr:
                    self_addrs.add(me_addr)
            except Exception as e:
                log.warning("could not fetch profile for self-exclusion: %s", e)
            try:
                sendas = gmail.users().settings().sendAs().list(userId="me").execute()
                for s in sendas.get("sendAs", []) or []:
                    addr = (s.get("sendAsEmail") or "").lower()
                    if addr:
                        self_addrs.add(addr)
            except Exception as e:
                log.warning("could not fetch send-as list for self-exclusion: %s", e)

            log.info(
                "workflow_enrich_contacts_from_recent_mail: preloaded %d saved contacts",
                len(preloaded_contacts),
            )

            # 1. List message IDs.
            message_ids: list[str] = []
            page_token = None
            fetched = 0
            while fetched < params.limit_messages_scanned:
                kwargs: dict[str, Any] = {
                    "userId": "me",
                    "q": query,
                    "maxResults": min(500, params.limit_messages_scanned - fetched),
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = gmail.users().messages().list(**kwargs).execute()
                batch = resp.get("messages", []) or []
                message_ids.extend(m["id"] for m in batch)
                fetched += len(batch)
                page_token = resp.get("nextPageToken")
                if not page_token or not batch:
                    break

            # 2. Fetch each message in full, keep newest-per-sender.
            newest_by_sender: dict[str, dict] = {}
            for mid in message_ids:
                try:
                    msg = gmail.users().messages().get(
                        userId="me", id=mid, format="full"
                    ).execute()
                except Exception as e:
                    log.warning("fetch %s failed: %s", mid, e)
                    continue
                headers = {
                    h.get("name", "").lower(): h.get("value", "")
                    for h in (msg.get("payload", {}) or {}).get("headers", []) or []
                }
                from_raw = headers.get("from", "")
                sender = _parse_sender(from_raw)
                if not sender:
                    continue
                internal_ts = int(msg.get("internalDate", "0"))
                existing = newest_by_sender.get(sender.lower())
                if not existing or int(existing.get("internalDate", "0")) < internal_ts:
                    newest_by_sender[sender.lower()] = msg

            # 3. Enrich each unique sender.
            results: list[dict] = []
            for sender_lower, msg in newest_by_sender.items():
                # Original-case email: pull from the headers once more.
                headers = {
                    h.get("name", "").lower(): h.get("value", "")
                    for h in (msg.get("payload", {}) or {}).get("headers", []) or []
                }
                from_raw = headers.get("from", "")
                sender = _parse_sender(from_raw) or sender_lower

                result = _enrich_one(
                    email=sender,
                    days=params.days,  # redundant — we already have the message — but passed for logging
                    overwrite=params.overwrite,
                    conservative_titles=params.conservative_titles,
                    dry_run=bool(is_dry_run(params.dry_run)),
                    preloaded_message=msg,
                    preloaded_contacts=preloaded_contacts,
                )

                # Handle not-yet-saved senders if caller wants auto-create.
                if (
                    result.get("status") == "skipped_no_saved_contact"
                    and not params.only_existing_contacts
                ):
                    # Self-exclude: never auto-create a contact for yourself
                    # (or any of your send-as aliases).
                    if sender.lower() in self_addrs:
                        result = {"email": sender, "status": "skipped_self"}
                        results.append(result)
                        continue

                    # Junk filter: don't auto-create contacts for marketing /
                    # noreply / notification senders.
                    body_text = _extract_plaintext_body(msg.get("payload") or {})
                    subject = headers.get("subject", "")
                    junk, reasons = is_junk(sender, headers, body_text, subject)
                    if junk:
                        log.info(
                            "bulk_enrich: skipping junk auto-create for %s: %s",
                            sender, reasons,
                        )
                        result = {
                            "email": sender,
                            "status": "skipped_junk_sender",
                            "junk_reasons": reasons,
                        }
                    elif is_dry_run(params.dry_run):
                        # Dry-run: report what would be created without writing.
                        _enh, _force = _signature_llm_flags()
                        preview_parsed = parse_signature_fields(
                            body_text,
                            known_email=sender,
                            conservative_titles=params.conservative_titles,
                            enhance_with_llm=_enh, force_llm=_force,
                        )
                        preview_parsed.pop("raw_signature", None)
                        result = {
                            "email": sender,
                            "status": "would_create",
                            "parsed_fields": preview_parsed,
                        }
                    else:
                        created = _auto_create_from_signature(
                            sender, from_raw, msg,
                            conservative_titles=params.conservative_titles,
                        )
                        result = created

                results.append(result)

            summary = {
                "query": query,
                "messages_scanned": len(message_ids),
                "unique_senders": len(newest_by_sender),
                "results": results,
                "counts": {
                    "updated": sum(1 for r in results if r.get("status") == "updated"),
                    "no_changes_needed": sum(
                        1 for r in results if r.get("status") == "no_changes_needed"
                    ),
                    "skipped_no_saved_contact": sum(
                        1 for r in results if r.get("status") == "skipped_no_saved_contact"
                    ),
                    "skipped_junk_sender": sum(
                        1 for r in results if r.get("status") == "skipped_junk_sender"
                    ),
                    "skipped_self": sum(
                        1 for r in results if r.get("status") == "skipped_self"
                    ),
                    "skipped_junk_message": sum(
                        1 for r in results if r.get("status") == "skipped_junk_message"
                    ),
                    "failed": sum(1 for r in results if r.get("status") == "failed"),
                    "auto_created": sum(1 for r in results if r.get("status") == "created"),
                    "would_create": sum(
                        1 for r in results if r.get("status") == "would_create"
                    ),
                },
            }
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_enrich_contacts_from_recent_mail", summary
                )
            return json.dumps(summary, indent=2)
        except Exception as e:
            log.error("workflow_enrich_contacts_from_recent_mail failed: %s", e)
            return format_error(e)


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #


def _parse_sender(raw_from: str) -> str | None:
    """Extract bare email from a 'Name <addr>' or plain 'addr' From header."""
    raw = (raw_from or "").strip()
    if not raw:
        return None
    if "<" in raw and ">" in raw:
        return raw[raw.rfind("<") + 1 : raw.rfind(">")].strip()
    return raw if "@" in raw else None


def _auto_create_from_signature(
    email: str,
    from_header: str,
    msg: dict,
    *,
    conservative_titles: bool,
) -> dict:
    """Create a brand-new contact and immediately enrich it from the signature."""
    body_text = _extract_plaintext_body(msg.get("payload") or {})
    enhance, force = _signature_llm_flags()
    parsed = parse_signature_fields(
        body_text, known_email=email,
        conservative_titles=conservative_titles,
        enhance_with_llm=enhance, force_llm=force,
    )

    # Name priority: parsed > display name from From header > email local-part.
    first_name = parsed.get("first_name") or ""
    last_name = parsed.get("last_name") or ""
    if not first_name:
        # Try the display name from the From header.
        display = ""
        if "<" in from_header:
            display = from_header.split("<", 1)[0].strip().strip('"')
            if display.lower() == email.lower():
                display = ""
        if display:
            parts = display.split()
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
    if not first_name:
        local = email.split("@", 1)[0]
        bits = [p for p in re.split(r"[._\-]+", local) if p]
        if bits:
            first_name = bits[0].capitalize()
            if len(bits) > 1:
                last_name = " ".join(b.capitalize() for b in bits[1:])

    # Build the create body.
    body: dict = {"emailAddresses": [{"value": email, "type": "work"}]}
    name_entry: dict = {}
    if first_name:
        name_entry["givenName"] = first_name
    if last_name:
        name_entry["familyName"] = last_name
    if name_entry:
        body["names"] = [name_entry]
    if parsed.get("organization") or parsed.get("title"):
        org_entry: dict = {}
        if parsed.get("organization"):
            org_entry["name"] = parsed["organization"]
        if parsed.get("title"):
            org_entry["title"] = parsed["title"]
        body["organizations"] = [org_entry]
    if parsed.get("phones"):
        body["phoneNumbers"] = [
            {"value": p, "type": "work" if i == 0 else "mobile"}
            for i, p in enumerate(parsed["phones"])
        ]
    if parsed.get("urls"):
        body["urls"] = [
            {"value": u, "type": "work" if i == 0 else "other"}
            for i, u in enumerate(parsed["urls"])
        ]
    # Socials + alt_emails → custom fields.
    custom: dict = {}
    for k in ("linkedin", "twitter", "facebook", "instagram"):
        v = parsed.get(k)
        if v:
            custom[k] = v
    if parsed.get("alt_emails"):
        custom["alt_emails"] = ", ".join(parsed["alt_emails"][:5])
    if custom:
        body["userDefined"] = [{"key": k, "value": v} for k, v in custom.items()]

    try:
        person = _people().people().createContact(
            body=body,
            personFields="names,emailAddresses,organizations,phoneNumbers,urls,userDefined,metadata",
        ).execute()
        log.info("auto-created contact from signature: %s (%s)", email, person.get("resourceName"))
        return {
            "email": email,
            "resource_name": person.get("resourceName"),
            "status": "created",
            "first_name": first_name,
            "last_name": last_name,
            "parsed_fields": {k: v for k, v in parsed.items() if k != "raw_signature"},
        }
    except Exception as e:
        log.error("auto-create from signature failed for %s: %s", email, e)
        return {"email": email, "status": "failed", "error": str(e)}
