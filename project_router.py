# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AP-5: Auto-project routing.

Given an inbound document signal (sender + subject + body + timestamp),
return the best-guess project_code with a confidence score. Used by
the Day-1 sweep loop to file receipts directly into the project's
Drive folder instead of dumping everything into Triage/.

Composes existing helpers from project_registry:

    Tier 1 — explicit code hint           1.00  (caller-supplied)
    Tier 2 — alias match in subject/body  0.92  (resolve_by_alias)
    Tier 3 — sender on team               0.88  (resolve_by_team_email)
              ↳ if multiple matches, calendar tiebreaker
    Tier 4 — calendar context at time     0.80  (which project's
                                                  customer event was
                                                  on the calendar?)
    Tier 5 — Geotab GPS position          0.85  (placeholder hook)
    Tier 6 — LLM inference                variable (project_registry
                                                    resolve use_llm)
    Tier 7 — unresolved → chat picker     None

The chat-picker fallback isn't a fully synchronous tier — when route
returns tier="chat_picker", the caller (sweep loop) is expected to
post a Receipts-space message asking the submitter to choose from a
short list. Their reply re-enters the loop with explicit_code.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

import project_registry


@dataclass
class RouteResult:
    project_code: Optional[str]
    confidence: float
    tier: str             # 'explicit'|'alias'|'team'|'calendar'|'geotab'|'llm'|'chat_picker'|'unresolved'
    reason: str
    candidates: list[dict]  # multiple-match cases keep the full list for fallback UIs


# Confidence floor — below this we route to chat picker rather than file blindly.
_AUTO_FILE_THRESHOLD = 0.85


# =============================================================================
# Public entry point
# =============================================================================

def route_project(
    *,
    sender_email: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    timestamp: Optional[_dt.datetime] = None,
    explicit_code: Optional[str] = None,
    use_llm: bool = True,
) -> RouteResult:
    """Resolve which project an inbound doc belongs to.

    Returns a RouteResult. Caller behavior based on tier:
        - confidence >= 0.85: auto-file into project's receipts folder
        - 0.65 <= confidence < 0.85: auto-file but flag for review
        - confidence < 0.65 OR tier='chat_picker': chat-back to submitter
          with a project picker
        - tier='unresolved': dump to Triage/ for operator
    """
    # Tier 1: explicit hint.
    if explicit_code:
        rec = project_registry.get(explicit_code)
        if rec:
            return RouteResult(
                project_code=rec["code"],
                confidence=1.0,
                tier="explicit",
                reason=f"caller-supplied code={explicit_code!r}",
                candidates=[rec],
            )

    # Tier 2: alias match against subject + body.
    haystack = " ".join(filter(None, [subject, body]))
    if haystack:
        match = project_registry.resolve_by_alias(haystack)
        if match:
            return RouteResult(
                project_code=match["code"],
                confidence=project_registry.CONF_ALIAS,
                tier="alias",
                reason=f"alias match in subject/body: {match.get('name')!r}",
                candidates=[match],
            )

    # Tier 3: sender on team list.
    team_matches: list[dict] = []
    if sender_email:
        team_matches = project_registry.resolve_by_team_email(sender_email)
    if len(team_matches) == 1:
        return RouteResult(
            project_code=team_matches[0]["code"],
            confidence=project_registry.CONF_TEAM,
            tier="team",
            reason=f"sender {sender_email!r} on single team",
            candidates=team_matches,
        )

    # Tier 4: calendar context — only when team match is ambiguous.
    if len(team_matches) > 1 and timestamp and sender_email:
        cal_winner = _calendar_tiebreaker(
            sender_email, timestamp, team_matches
        )
        if cal_winner:
            return RouteResult(
                project_code=cal_winner["code"],
                confidence=0.80,
                tier="calendar",
                reason=(
                    f"team match ambiguous ({len(team_matches)} candidates); "
                    f"calendar at {timestamp.isoformat()} disambiguated to "
                    f"{cal_winner.get('name')!r}"
                ),
                candidates=team_matches,
            )

    # Tier 5: Geotab GPS position. Stubbed — wire up when GEOTAB_*
    # credentials land in config.
    geo_winner = _geotab_tiebreaker(sender_email, timestamp, team_matches)
    if geo_winner:
        return RouteResult(
            project_code=geo_winner["code"],
            confidence=0.85,
            tier="geotab",
            reason=f"vehicle GPS placed cardholder near {geo_winner.get('name')!r}",
            candidates=team_matches,
        )

    # Tier 6: LLM inference over the doc text (delegates to existing
    # project_registry.resolve which knows how to call Claude-haiku).
    if use_llm and (subject or body):
        result = project_registry.resolve(
            sender_email=sender_email,
            invoice_text=" ".join(filter(None, [subject, body]))[:2000],
            use_llm=True,
        )
        if result.project_code and result.tier == "llm":
            return RouteResult(
                project_code=result.project_code,
                confidence=result.confidence,
                tier="llm",
                reason=result.reason,
                candidates=[],
            )

    # Tier 7: unresolved. If we had ambiguous team matches, surface them
    # as the picker list. Otherwise just signal Triage.
    if team_matches:
        return RouteResult(
            project_code=None,
            confidence=0.0,
            tier="chat_picker",
            reason=f"sender on {len(team_matches)} teams; no other signals",
            candidates=team_matches,
        )

    # Final: unresolved.
    return RouteResult(
        project_code=None,
        confidence=0.0,
        tier="unresolved",
        reason="no_signal_matched",
        candidates=[],
    )


def confidence_action(result: RouteResult) -> str:
    """Map a RouteResult to a sweep-loop action.

    Returns one of:
        'auto_file'         - confidence >= 0.85, just file it
        'auto_file_flag'    - 0.65-0.85, file + add to review queue
        'chat_picker'       - <0.65 OR tier='chat_picker', ask submitter
        'triage'            - unresolved, dump to Triage/
    """
    if result.tier in ("chat_picker", "unresolved"):
        return "chat_picker" if result.tier == "chat_picker" else "triage"
    if result.confidence >= _AUTO_FILE_THRESHOLD:
        return "auto_file"
    if result.confidence >= 0.65:
        return "auto_file_flag"
    return "chat_picker"


# =============================================================================
# Tiebreakers — calendar + Geotab
# =============================================================================

def _calendar_tiebreaker(
    cardholder_email: str,
    timestamp: _dt.datetime,
    candidates: list[dict],
) -> Optional[dict]:
    """Look up the cardholder's calendar at `timestamp`. If a meeting's
    attendees include any candidate project's customer_email or the
    meeting summary mentions a project name/alias, that's the winner.

    Returns None when calendar is empty or no candidate matches.
    """
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        return None

    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        # Window: ±2 hours around the timestamp.
        time_min = (timestamp - _dt.timedelta(hours=2)).isoformat()
        time_max = (timestamp + _dt.timedelta(hours=2)).isoformat()
        events = service.events().list(
            calendarId=cardholder_email,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute().get("items", [])
    except Exception:
        return None

    for event in events:
        summary = (event.get("summary") or "").lower()
        attendees_emails = [
            (a.get("email") or "").lower()
            for a in (event.get("attendees") or [])
        ]
        for proj in candidates:
            # Match by project name / alias in event summary.
            for token in [proj.get("name") or ""] + list(
                proj.get("name_aliases") or []
            ):
                if token and token.lower() in summary:
                    return proj
            # Match by customer email on the invitee list.
            cust = (proj.get("customer_email") or "").lower()
            if cust and cust in attendees_emails:
                return proj
    return None


def _geotab_tiebreaker(
    cardholder_email: Optional[str],
    timestamp: Optional[_dt.datetime],
    candidates: list[dict],
) -> Optional[dict]:
    """Geotab GPS position lookup — STUB.

    Real impl: query Geotab for the cardholder's vehicle position at
    `timestamp`, reverse-geocode, match against project sites. Wire up
    when Geotab API credentials are in config.

    For now: return None so the router proceeds to LLM inference.
    """
    # TODO: Geotab integration. Tracking config:
    #   config.geotab_database, config.geotab_username, config.geotab_password
    return None
