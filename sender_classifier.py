# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""Internal vs external sender classification.

Used by the project-invoice extractor to decide whether to auto-reply with
an info request when the quality guard fires:

  - INTERNAL sender (employee / project manager submitting an invoice for
    payment) → safe to reply directly via Chat DM or email; the employee
    is paid to be accurate so we shouldn't be shy about asking for what's
    missing.
  - EXTERNAL sender (vendor or client) → MCP must NEVER auto-reply.
    Vendor/client conversations are handled by reps, not by automation.
    The row gets parked for human handoff.

Sources of "internal" status (in priority order):

  1. Sender's email domain matches the authenticated user's domain
     (auto-derived from token.json / People API on first call, cached
     for the process lifetime).
  2. Sender's domain appears in `config.internal_domains` (override / extend).
  3. Sender's domain appears in `config.subsidiary_domains` (treated
     identically to internal but tracked separately for reporting).
  4. Sender's email matches one of the user's Gmail send-as aliases
     (catches the case where the user forwards from another mailbox).

The first match wins. Lookup is cheap (string compare); the only paid call
is the initial domain auto-derive, and that's cached.
"""

from __future__ import annotations

import re
from typing import Optional


# Process-lifetime caches. Cleared on server restart.
_AUTO_DERIVED_DOMAIN: Optional[str] = None
_SEND_AS_ALIASES: Optional[set[str]] = None
_DOMAIN_RE = re.compile(r"@([A-Z0-9._-]+)$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _extract_email(s: str) -> str:
    """Pull the email out of an RFC 5322 'Name <addr@host>' header. Lowercase."""
    if not s:
        return ""
    s = s.strip()
    if "<" in s and ">" in s:
        s = s.split("<", 1)[1].split(">", 1)[0]
    return s.strip().lower()


def _domain_of(email: str) -> str:
    """Return the lowercased domain of an email, or empty string."""
    if not email:
        return ""
    m = _DOMAIN_RE.search(email)
    return m.group(1).lower() if m else ""


def _norm_domain_list(items) -> set[str]:
    """Lowercase + strip whitespace + drop empties from a list of domains."""
    if not items:
        return set()
    return {d.strip().lower().lstrip("@") for d in items if d and d.strip()}


def _user_domain() -> Optional[str]:
    """Authenticated user's primary email domain. Tries Gmail's getProfile
    first (most reliable — already used elsewhere in this codebase),
    People API as a fallback. Caches the resolved value for the process
    lifetime; on failure, retries on the next call instead of caching the
    miss (the first failure is often a transient OAuth refresh blip)."""
    global _AUTO_DERIVED_DOMAIN
    if _AUTO_DERIVED_DOMAIN:
        return _AUTO_DERIVED_DOMAIN

    # Tier 1: Gmail getProfile — returns 'emailAddress' as a top-level field.
    try:
        import gservices
        gmail = gservices.gmail()
        prof = gmail.users().getProfile(userId="me").execute()
        addr = (prof.get("emailAddress") or "").strip().lower()
        if addr and "@" in addr:
            _AUTO_DERIVED_DOMAIN = _domain_of(addr)
            return _AUTO_DERIVED_DOMAIN
    except Exception:
        pass

    # Tier 2: People API people/me
    try:
        import gservices
        people = gservices.people()
        prof = people.people().get(
            resourceName="people/me",
            personFields="emailAddresses",
        ).execute()
        emails = prof.get("emailAddresses") or []
        for e in emails:
            val = (e.get("value") or "").strip().lower()
            if val and "@" in val:
                _AUTO_DERIVED_DOMAIN = _domain_of(val)
                return _AUTO_DERIVED_DOMAIN
    except Exception:
        pass

    # No cache on failure — retry next call.
    return None


def _send_as_aliases() -> set[str]:
    """Authenticated user's Gmail send-as aliases (other email addresses
    they're authorized to send from). Cached for the process lifetime.
    Returns lowercased addresses."""
    global _SEND_AS_ALIASES
    if _SEND_AS_ALIASES is not None:
        return _SEND_AS_ALIASES
    try:
        import gservices
        gmail = gservices.gmail()
        resp = gmail.users().settings().sendAs().list(userId="me").execute()
        out: set[str] = set()
        for entry in resp.get("sendAs", []) or []:
            addr = (entry.get("sendAsEmail") or "").strip().lower()
            if addr:
                out.add(addr)
        _SEND_AS_ALIASES = out
        return _SEND_AS_ALIASES
    except Exception:
        _SEND_AS_ALIASES = set()
        return _SEND_AS_ALIASES


def _config_internal_domains() -> set[str]:
    """User-overridden / extended internal domains from config.json."""
    try:
        import config
        return _norm_domain_list(config.get("internal_domains", []))
    except Exception:
        return set()


def _config_subsidiary_domains() -> set[str]:
    """Subsidiary domains — treated identically to internal."""
    try:
        import config
        return _norm_domain_list(config.get("subsidiary_domains", []))
    except Exception:
        return set()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def internal_domains() -> set[str]:
    """All domains currently considered internal — for diagnostics + tests.

    Union of: auto-derived user domain, config.internal_domains,
    config.subsidiary_domains.
    """
    out: set[str] = set()
    auto = _user_domain()
    if auto:
        out.add(auto)
    out |= _config_internal_domains()
    out |= _config_subsidiary_domains()
    return out


def classify(sender: str) -> dict:
    """Classify a sender as internal or external. Returns dict with shape:
        {
          "internal":   bool,
          "tier":       'auto_domain' | 'config_internal' |
                        'config_subsidiary' | 'send_as_alias' |
                        'external',
          "reason":     short string,
          "email":      normalized sender email,
          "domain":     domain part,
        }

    Invariant: caller can safely route based on `internal == True`.
    """
    email = _extract_email(sender)
    domain = _domain_of(email)
    out = {
        "internal": False, "tier": "external",
        "reason": "no_match", "email": email, "domain": domain,
    }
    if not email or not domain:
        out["reason"] = "no_email_address"
        return out

    # Tier 1: auto-derived user domain
    auto = _user_domain()
    if auto and domain == auto:
        out.update({
            "internal": True, "tier": "auto_domain",
            "reason": f"matches authenticated user's domain ({auto})",
        })
        return out

    # Tier 2: config.internal_domains
    if domain in _config_internal_domains():
        out.update({
            "internal": True, "tier": "config_internal",
            "reason": f"matches config.internal_domains ({domain})",
        })
        return out

    # Tier 3: config.subsidiary_domains
    if domain in _config_subsidiary_domains():
        out.update({
            "internal": True, "tier": "config_subsidiary",
            "reason": f"matches config.subsidiary_domains ({domain})",
        })
        return out

    # Tier 4: send-as alias (rare — usually catches user-forwarded mail)
    if email in _send_as_aliases():
        out.update({
            "internal": True, "tier": "send_as_alias",
            "reason": f"matches a Gmail send-as alias ({email})",
        })
        return out

    out["reason"] = (
        f"domain {domain!r} not in any internal allowlist "
        f"(auto, internal_domains, subsidiary_domains, send_as)"
    )
    return out


def is_internal(sender: str) -> bool:
    """Convenience wrapper — True if classify() returns internal."""
    return classify(sender).get("internal", False)


# --------------------------------------------------------------------------- #
# Test helpers — let unit tests bypass the live Google calls.
# --------------------------------------------------------------------------- #


def _override_for_tests(
    *,
    auto_domain: Optional[str] = None,
    send_as: Optional[set[str]] = None,
) -> None:
    global _AUTO_DERIVED_DOMAIN, _SEND_AS_ALIASES
    _AUTO_DERIVED_DOMAIN = auto_domain
    _SEND_AS_ALIASES = send_as


def _reset_for_tests() -> None:
    global _AUTO_DERIVED_DOMAIN, _SEND_AS_ALIASES
    _AUTO_DERIVED_DOMAIN = None
    _SEND_AS_ALIASES = None
