# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Contacts/CRM-driven workflows (enrichment, bulk update, geocode, density).

Split from the legacy tools/workflows.py during P1-1
(see mcp-design-docs-2026-04-29.md). All shared helpers live
in tools/_workflow_helpers.py.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Optional

from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
from pydantic import BaseModel, ConfigDict, Field

import config
import crm_stats
import gservices
import rendering
import templates as templates_mod
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log
from tools.contacts import _flatten_person  # noqa: E402 — reuse the flattening logic

# Inline MIME builder import — we can't cleanly import from tools.gmail without
# a circular import, so we use the email stdlib directly here.
import mimetypes
from email.message import EmailMessage

# Shared helpers from the legacy workflows.py
from tools._workflow_helpers import (
    _contact_lat_lng,
    _extract_address_block,
    _extract_display_name,
    _gmail,
    _guess_name_from_email,
    _haversine_km,
    _parse_email_address,
    _set_contact_custom_fields,
    _split_display_name,
    _walk_all_contacts,
)

# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class SeedResponseTemplatesInput(BaseModel):
    """Input for workflow_seed_response_templates."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    force: bool = Field(
        default=False,
        description=(
            "Overwrite templates that already exist. Default False so the "
            "tool never silently clobbers a user-edited template."
        ),
    )
    only: Optional[str] = Field(
        default=None,
        description=(
            "If set, generate only this one slug (e.g. 'welcome_response'). "
            "If unset, generates all 8 starter response templates."
        ),
    )


class CreateContactsFromSentMailInput(BaseModel):
    """Input for workflow_create_contacts_from_sent_mail."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description="How many days back to scan sent mail. Default 30.",
    )
    limit_emails_scanned: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Max messages to scan from 'in:sent' (newest first). Default 500.",
    )
    skip_existing: bool = Field(
        default=True,
        description="Skip addresses that already exist as saved contacts.",
    )
    exclude_domains: Optional[list[str]] = Field(
        default=None,
        description="Domains to exclude (e.g. 'noreply.com'). Matched case-insensitively.",
    )
    exclude_self: bool = Field(
        default=True,
        description="Exclude your own address and any configured send-as aliases.",
    )
    only_domains: Optional[list[str]] = Field(
        default=None,
        description="If set, ONLY create contacts whose address matches one of these domains.",
    )
    apply_rules_after: bool = Field(
        default=True,
        description="After creating contacts, run contacts_apply_rules so auto-tagging fires.",
    )
    enrich_from_inbox: bool = Field(
        default=False,
        description="After creating contacts, enrich each one from their most recent inbound "
                    "email signature (title, phone E.164, website, social URLs, etc.).",
    )
    enrich_days: int = Field(
        default=180,
        ge=1,
        le=3650,
        description="When enrich_from_inbox is True: how far back to look for a message from "
                    "each new contact.",
    )
    dry_run: Optional[bool] = Field(default=None)


class NearbyContactsInput(BaseModel):
    """Input for workflow_nearby_contacts."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    location: str = Field(
        ..., description="Address, place name, or 'lat,lng' to search around.",
    )
    radius_km: float = Field(
        default=25.0, gt=0, le=500,
        description="Radius in kilometers. Default 25.",
    )
    limit: int = Field(default=20, ge=1, le=100)
    sort_by: str = Field(
        default="distance",
        description="'distance' or 'recency' (most recently contacted first).",
    )
    only_groups: Optional[list[str]] = Field(
        default=None,
        description="Restrict to contacts in these contactGroup resource names.",
    )
    require_geocoded: bool = Field(
        default=False,
        description=(
            "If True, only consider contacts that already have stored lat/lng custom fields "
            "(fast). If False, geocode on-the-fly for any contact missing them (costs API calls)."
        ),
    )
    include_travel_time: bool = Field(
        default=False,
        description="If True, compute live driving time from `location` to top results via Distance Matrix.",
    )
    travel_mode: str = Field(default="driving")


class GeocodeContactsBatchInput(BaseModel):
    """Input for workflow_geocode_contacts_batch."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    only_groups: Optional[list[str]] = Field(
        default=None,
        description="Restrict to contacts in these contactGroup resource names.",
    )
    force: bool = Field(
        default=False,
        description="If True, re-geocode contacts that already have stored lat/lng.",
    )
    max_contacts: int = Field(
        default=500, ge=1, le=5000,
        description="Safety cap on how many contacts to process in one run.",
    )
    dry_run: Optional[bool] = Field(default=None)


class AddressHygieneAuditInput(BaseModel):
    """Input for workflow_address_hygiene_audit."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    only_groups: Optional[list[str]] = Field(
        default=None,
        description="Restrict to contacts in these contactGroup resource names.",
    )
    max_contacts: int = Field(default=500, ge=1, le=5000)
    region_code: Optional[str] = Field(
        default=None,
        description="ISO-3166 country code to bias validation, e.g. 'US'.",
    )
    write_to_sheet: bool = Field(
        default=True,
        description="If True, write the report to a new Google Sheet and return the URL.",
    )
    sheet_title: Optional[str] = Field(default=None)


class ContactDensityMapInput(BaseModel):
    """Input for workflow_contact_density_map."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    only_groups: Optional[list[str]] = Field(default=None)
    region_filter: Optional[str] = Field(
        default=None,
        description="Free-text filter applied to contact city/region (e.g. 'CA' or 'Austin').",
    )
    max_markers: int = Field(default=80, ge=1, le=500)
    size: str = Field(default="640x640")
    map_type: str = Field(default="roadmap")
    save_to_path: Optional[str] = Field(
        default=None,
        description="If set, write PNG to this absolute path. Otherwise return base64.",
    )


class BulkContactPatch(BaseModel):
    """One target+patch for workflow_bulk_update_contacts."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(
        ..., description="Contact resource_name (e.g. 'people/c123abc456').",
    )
    patch: dict = Field(
        ...,
        description=(
            "Fields to update on this contact. Supported keys: organization, "
            "title, notes, custom_fields (dict). Set a key to null to clear."
        ),
    )


class BulkUpdateContactsInput(BaseModel):
    """Input for workflow_bulk_update_contacts."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    updates: list[BulkContactPatch] = Field(..., min_length=1, max_length=500)
    rollback_on_partial_failure: bool = Field(
        default=True,
        description=(
            "If any individual update fails, restore all already-applied "
            "updates from snapshots taken before the run. Atomic-ish — best "
            "you can do without a real transaction."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class SuggestResponseTemplateInput(BaseModel):
    """Input for workflow_suggest_response_template."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message_id: Optional[str] = Field(
        default=None,
        description="Gmail message ID. Either this or `text` is required.",
    )
    text: Optional[str] = Field(
        default=None,
        description="Raw text to classify (subject + body). Use when no Gmail thread.",
    )


class SmartFollowupFinderInput(BaseModel):
    """Input for workflow_smart_followup_finder."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    days_stale: int = Field(
        default=7, ge=1, le=90,
        description="Threads with no reply in at least this many days are candidates.",
    )
    max_threads: int = Field(
        default=20, ge=1, le=100,
        description="Cap on candidate threads (Gmail search size).",
    )
    only_external: bool = Field(
        default=True,
        description="Skip threads where last sender is from your own domain.",
    )
    create_drafts: bool = Field(
        default=False,
        description="If True, create a Gmail draft for each candidate using the suggested template.",
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_seed_response_templates",
        annotations={
            "title": "Seed 8 brand-voice response templates as HTML",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_seed_response_templates(
        params: SeedResponseTemplatesInput,
    ) -> str:
        """Generate 8 reusable response templates in your brand voice.

        Reads `brand-voice.md` for tone, then drafts 8 HTML email templates
        via Claude Haiku and writes them to `templates/`:

          - customer_complaint_response
          - upset_client_response
          - thanks_for_reply
          - great_to_meet_you
          - client_feedback_response
          - welcome_response
          - renewal_reminder_response
          - followup_response

        Existing templates are PRESERVED unless `force=true`. Each generated
        template carries an `html_body` plus a plain-text body so the
        send-templated tools can pick whichever the recipient supports.

        Cost: ~$0.02 for the full set (8 Haiku calls). Free first run, since
        you can re-run with `force=true` later if you want a fresh draft
        after updating brand-voice.md.
        """
        try:
            # Re-use the script's logic without shelling out so the MCP can
            # produce a structured JSON result.
            import sys as _sys
            scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
            if scripts_dir not in _sys.path:
                _sys.path.insert(0, scripts_dir)
            import seed_response_templates as _seed

            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({"status": "no_llm", "reason": reason}, indent=2)

            brand_voice = _seed._read_brand_voice()
            templates_dir = Path(_seed._PROJECT) / "templates"
            templates_dir.mkdir(exist_ok=True)

            cats = _seed.CATEGORIES
            if params.only:
                cats = [c for c in _seed.CATEGORIES if c["slug"] == params.only]
                if not cats:
                    return json.dumps({
                        "status": "unknown_slug",
                        "requested": params.only,
                        "available": [c["slug"] for c in _seed.CATEGORIES],
                    }, indent=2)

            results: list[dict] = []
            created = skipped = failed = 0
            for category in cats:
                slug = category["slug"]
                path = templates_dir / f"{slug}.md"
                if path.exists() and not params.force:
                    skipped += 1
                    results.append({"slug": slug, "status": "skipped",
                                    "reason": "already_exists"})
                    continue
                try:
                    generated = _seed._generate_one(category, brand_voice)
                    body = _seed._render_template_file(category, generated)
                    path.write_text(body, encoding="utf-8")
                    created += 1
                    results.append({
                        "slug": slug, "status": "created",
                        "subject": generated.get("subject"),
                        "description": generated.get("description"),
                    })
                except Exception as e:
                    log.warning("seed template %s failed: %s", slug, e)
                    failed += 1
                    results.append({"slug": slug, "status": "failed",
                                    "error": str(e)})
            return json.dumps({
                "status": "ok" if failed == 0 else "partial",
                "summary": {
                    "created": created,
                    "skipped": skipped,
                    "failed": failed,
                    "total_evaluated": len(cats),
                },
                "templates": results,
                "estimated_cost_usd": round(created * 0.0025, 4),
                "hint": (
                    "Templates land in templates/*.md. Use "
                    "gmail_get_template to inspect, gmail_send_templated_by_name "
                    "to send. Existing files were preserved (pass force=true to "
                    "regenerate)."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_seed_response_templates failed: %s", e)
            return format_error(e)

    # --- Receipt + brand-voice composed workflows --------------------------- #

    @mcp.tool(
        name="workflow_suggest_response_template",
        annotations={
            "title": "Pick the right inbound_* template for an email + draft a reply",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_suggest_response_template(
        params: SuggestResponseTemplateInput,
    ) -> str:
        """Classify an inbound email and recommend the closest inbound_*
        template. Returns the slug + a draft body with placeholders resolved
        from the sender (when message_id is provided)."""
        try:
            import llm as _llm
            ok, why = _llm.is_available()
            if not ok:
                return json.dumps({"status": "no_llm", "reason": why}, indent=2)

            # Resolve content
            subject = ""
            sender = ""
            body_text = params.text or ""
            if params.message_id and not body_text:
                full = gservices.gmail().users().messages().get(
                    userId="me", id=params.message_id, format="full",
                ).execute()
                hdrs = {h["name"].lower(): h["value"]
                        for h in (full.get("payload", {}).get("headers") or [])}
                subject = hdrs.get("subject", "")
                sender = hdrs.get("from", "")
                body_text = full.get("snippet", "")
            if not body_text and not params.text:
                return json.dumps({
                    "status": "no_content",
                    "hint": "Pass message_id or text.",
                }, indent=2)

            # List candidate inbound_* templates
            all_templates = templates_mod.list_templates()
            candidates = [
                t for t in all_templates
                if t.get("name", "").startswith("inbound_")
            ]
            if not candidates:
                return json.dumps({
                    "status": "no_templates",
                    "hint": (
                        "No inbound_* templates exist yet. Run "
                        "workflow_seed_response_templates first."
                    ),
                }, indent=2)

            cand_list = "\n".join(
                f"- {c['name']}: {c.get('description', '(no description)')}"
                for c in candidates
            )
            prompt = (
                "An inbound email needs a reply. Pick the SINGLE closest "
                "template from the candidates below. Return ONLY a JSON "
                "object — no prose:\n\n"
                "{\n"
                '  "template_name": "<exact slug from candidates>",\n'
                '  "confidence": <0.0-1.0>,\n'
                '  "reasoning": "<one sentence>"\n'
                "}\n\n"
                f"Candidates:\n{cand_list}\n\n"
                f"Inbound message:\nSubject: {subject}\nFrom: {sender}\n"
                f"Body: {body_text[:2000]}"
            )
            resp = _llm.call_simple(prompt, model="claude-haiku-4-5", max_tokens=300)
            try:
                pick = json.loads(
                    resp["text"].strip().strip("`").lstrip("json").strip()
                )
            except Exception:
                pick = {"template_name": candidates[0]["name"],
                        "confidence": 0.0,
                        "reasoning": f"LLM returned unparseable JSON: {resp['text'][:120]}"}

            chosen = pick.get("template_name", "")
            if chosen not in {c["name"] for c in candidates}:
                chosen = candidates[0]["name"]
                pick["confidence"] = 0.0
                pick["reasoning"] = (
                    f"LLM picked unknown slug; defaulted to {chosen}"
                )

            try:
                tpl = templates_mod.load(chosen)
            except templates_mod.TemplateError as e:
                return json.dumps({
                    "status": "template_load_failed",
                    "template_name": chosen,
                    "error": str(e),
                }, indent=2)

            return json.dumps({
                "status": "ok",
                "suggested_template": chosen,
                "confidence": pick.get("confidence"),
                "reasoning": pick.get("reasoning"),
                "draft": {
                    "subject": tpl.subject,
                    "body": tpl.body,
                    "html_body": tpl.html_body,
                },
                "sender": sender,
                "candidates_considered": [c["name"] for c in candidates],
            }, indent=2)
        except Exception as e:
            log.error("workflow_suggest_response_template failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_smart_followup_finder",
        annotations={
            "title": "Find stale unresponded threads + suggest follow-up templates",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_smart_followup_finder(
        params: SmartFollowupFinderInput,
    ) -> str:
        """Sweep the inbox for threads where the last message is from an
        external contact and you haven't replied in `days_stale` days.
        Returns a list of candidates each paired with a suggested
        inbound_followup_response template draft. Optionally creates Gmail
        drafts (`create_drafts=true`)."""
        try:
            import datetime as _dt
            gmail = gservices.gmail()
            # Get our own email so we can detect 'external' senders
            try:
                profile = gmail.users().getProfile(userId="me").execute()
                my_email = (profile.get("emailAddress") or "").lower()
                my_domain = my_email.split("@")[-1] if "@" in my_email else ""
            except Exception:
                my_email = ""
                my_domain = ""

            # Gmail query: unread or unreplied threads older than N days
            since = (
                _dt.date.today() - _dt.timedelta(days=params.days_stale)
            ).strftime("%Y/%m/%d")
            q = f"-from:me older_than:{params.days_stale}d"
            search = gmail.users().messages().list(
                userId="me", q=q, maxResults=params.max_threads,
            ).execute()
            msgs = search.get("messages", []) or []

            candidates: list[dict] = []
            seen_threads: set[str] = set()
            for m in msgs[:params.max_threads]:
                full = gmail.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                tid = full.get("threadId")
                if tid in seen_threads:
                    continue
                seen_threads.add(tid)
                hdrs = {h["name"].lower(): h["value"]
                        for h in (full.get("payload", {}).get("headers") or [])}
                sender = hdrs.get("from", "")
                # External check
                if params.only_external and my_domain:
                    sender_l = sender.lower()
                    if my_domain in sender_l:
                        continue
                candidates.append({
                    "thread_id": tid,
                    "message_id": m["id"],
                    "subject": hdrs.get("subject", ""),
                    "from": sender,
                    "date": hdrs.get("date", ""),
                    "snippet": full.get("snippet", ""),
                    "suggested_template": "inbound_followup_response",
                })

            # Optionally create drafts
            drafts_created = 0
            if params.create_drafts and candidates:
                try:
                    tpl = templates_mod.load("inbound_followup_response")
                except templates_mod.TemplateError:
                    tpl = None
                if tpl:
                    from email.message import EmailMessage
                    for c in candidates[:10]:  # cap at 10 drafts
                        try:
                            msg = EmailMessage()
                            msg["To"] = c["from"]
                            msg["Subject"] = f"Re: {c['subject']}"
                            msg.set_content(tpl.body)
                            raw = base64.urlsafe_b64encode(
                                msg.as_bytes()
                            ).decode("ascii")
                            gmail.users().drafts().create(
                                userId="me",
                                body={"message": {
                                    "raw": raw, "threadId": c["thread_id"],
                                }},
                            ).execute()
                            drafts_created += 1
                        except Exception as e:
                            log.warning("draft creation failed: %s", e)

            return json.dumps({
                "status": "ok",
                "candidates": candidates,
                "count": len(candidates),
                "drafts_created": drafts_created,
                "hint": (
                    "Pass create_drafts=true to auto-draft replies. The "
                    "inbound_followup_response template fills with placeholders "
                    "you can edit before sending."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_smart_followup_finder failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_create_contacts_from_sent_mail",
        annotations={
            "title": "Create saved contacts from recent sent mail",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_create_contacts_from_sent_mail(
        params: CreateContactsFromSentMailInput,
    ) -> str:
        """Bulk-populate saved contacts from your sent mail history.

        Scans `in:sent newer_than:<days>d`, extracts the To/Cc addresses (and
        display names) across those messages, dedupes against your existing
        saved contacts, and creates a new saved contact for every new address.

        Notes:
          - Universe is your Saved Contacts (People API `connections`). "Other
            contacts" are the auto-suggested directory; they're not touched.
          - `exclude_self` also excludes every address returned by Gmail's
            send-as list.
          - With `apply_rules_after=True`, domain-based auto-tagging rules
            fire after creation so organization/tier fields get filled in.
          - Supports `dry_run` — returns the planned additions without writing.
        """
        try:
            gmail = _gmail()
            people = gservices.people()

            # 1. Gather exclusion set: self + send-as + domain blocklist.
            exclude_addrs: set[str] = set()
            if params.exclude_self:
                try:
                    prof = gmail.users().getProfile(userId="me").execute()
                    me_addr = (prof.get("emailAddress") or "").lower()
                    if me_addr:
                        exclude_addrs.add(me_addr)
                except Exception as e:
                    log.warning("could not fetch my profile for exclusion: %s", e)
                try:
                    sendas = (
                        gmail.users().settings().sendAs().list(userId="me").execute()
                    )
                    for s in sendas.get("sendAs", []) or []:
                        addr = (s.get("sendAsEmail") or "").lower()
                        if addr:
                            exclude_addrs.add(addr)
                except Exception as e:
                    log.warning("could not fetch send-as list: %s", e)

            exclude_doms = {
                d.strip().lower().lstrip("@")
                for d in (params.exclude_domains or [])
                if d and d.strip()
            }
            only_doms = {
                d.strip().lower().lstrip("@")
                for d in (params.only_domains or [])
                if d and d.strip()
            }

            # 2. Gather existing saved-contact addresses (for skip_existing).
            existing_addrs: set[str] = set()
            existing_resource_by_addr: dict[str, str] = {}
            if params.skip_existing:
                page_token = None
                while True:
                    kwargs = {
                        "resourceName": "people/me",
                        "personFields": "emailAddresses,metadata",
                        "pageSize": 1000,
                    }
                    if page_token:
                        kwargs["pageToken"] = page_token
                    resp = (
                        people.people().connections().list(**kwargs).execute()
                    )
                    for p in resp.get("connections", []) or []:
                        for ea in p.get("emailAddresses") or []:
                            v = (ea.get("value") or "").lower()
                            if v:
                                existing_addrs.add(v)
                                existing_resource_by_addr.setdefault(
                                    v, p.get("resourceName", "")
                                )
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break

            # 3. Scan sent mail for To/Cc addresses with display names.
            query = f"in:sent newer_than:{params.days}d"
            message_ids: list[str] = []
            page_token = None
            fetched = 0
            while fetched < params.limit_emails_scanned:
                remaining = params.limit_emails_scanned - fetched
                page_size = min(500, remaining)
                kwargs = {
                    "userId": "me",
                    "q": query,
                    "maxResults": page_size,
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

            # 4. For each message, fetch just the headers we need.
            #    Using metadata format keeps the payload tiny.
            candidates: dict[str, dict] = {}  # addr -> {display_name, first_seen_id}
            for mid in message_ids:
                try:
                    msg = (
                        gmail.users()
                        .messages()
                        .get(
                            userId="me",
                            id=mid,
                            format="metadata",
                            metadataHeaders=["To", "Cc"],
                        )
                        .execute()
                    )
                except Exception as e:
                    log.warning("fetch header for %s failed: %s", mid, e)
                    continue
                headers = {
                    h.get("name", "").lower(): h.get("value", "")
                    for h in (msg.get("payload", {}) or {}).get("headers", []) or []
                }
                for key in ("to", "cc"):
                    raw = headers.get(key, "")
                    if not raw:
                        continue
                    for part in raw.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        addr = _parse_email_address(part)
                        if not addr:
                            continue
                        addr_lower = addr.lower()
                        if addr_lower in exclude_addrs:
                            continue
                        domain = addr_lower.rsplit("@", 1)[-1] if "@" in addr_lower else ""
                        if exclude_doms and domain in exclude_doms:
                            continue
                        if only_doms and domain not in only_doms:
                            continue
                        if params.skip_existing and addr_lower in existing_addrs:
                            continue
                        display = _extract_display_name(part)
                        # If the display name is just the email address repeated
                        # (common form: "addr@x" <addr@x>), treat as no display name.
                        if display and display.lower() == addr_lower:
                            display = ""
                        if addr_lower not in candidates:
                            candidates[addr_lower] = {
                                "email": addr,  # preserve original case
                                "display_name": display,
                                "first_seen_message_id": mid,
                            }
                        elif display and not candidates[addr_lower].get("display_name"):
                            # Upgrade the display name if we find a better one later.
                            candidates[addr_lower]["display_name"] = display

            # 5. Build creation plan.
            plan = []
            for addr_lower, info in sorted(candidates.items()):
                display = info.get("display_name") or ""
                if display:
                    first, last = _split_display_name(display)
                else:
                    # No display name — guess from the email's local-part so
                    # the contact isn't anonymous.
                    first, last = _guess_name_from_email(info["email"])
                plan.append(
                    {
                        "email": info["email"],
                        "first_name": first,
                        "last_name": last,
                        "display_name": display,
                    }
                )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_create_contacts_from_sent_mail",
                    {
                        "query": query,
                        "messages_scanned": len(message_ids),
                        "existing_saved_contacts_checked": len(existing_addrs),
                        "to_create": len(plan),
                        "sample": plan[:20],
                    },
                )

            # 6. Create them, applying auto-tagging rules inline (same pattern as contacts_create).
            import rules as rules_mod  # lazy — avoid a module-level dep

            created: list[dict] = []
            failed: list[dict] = []
            rules_hits = 0
            for row in plan:
                # Apply rules: domain-based auto-tagging (organization, tier, custom fields, etc.).
                rule_top: dict = {}
                rule_custom: dict = {}
                rules_applied: list = []
                if params.apply_rules_after:
                    try:
                        rule_top, rule_custom, rules_applied = rules_mod.apply_rules(
                            row["email"],
                            existing_fields={
                                "first_name": row["first_name"] or None,
                                "last_name": row["last_name"] or None,
                                "organization": None,
                                "title": None,
                            },
                            existing_custom={},
                        )
                    except Exception as re:
                        log.warning("apply_rules for %s failed: %s", row["email"], re)

                first_name = row["first_name"] or rule_top.get("first_name") or ""
                last_name = row["last_name"] or rule_top.get("last_name") or ""

                body: dict = {
                    "emailAddresses": [{"value": row["email"], "type": "work"}],
                }
                name_part: dict = {}
                if first_name:
                    name_part["givenName"] = first_name
                if last_name:
                    name_part["familyName"] = last_name
                if name_part:
                    body["names"] = [name_part]
                org = rule_top.get("organization")
                title = rule_top.get("title")
                if org or title:
                    org_entry: dict = {}
                    if org:
                        org_entry["name"] = org
                    if title:
                        org_entry["title"] = title
                    body["organizations"] = [org_entry]
                if rule_custom:
                    body["userDefined"] = [
                        {"key": k, "value": str(v)} for k, v in rule_custom.items()
                    ]
                try:
                    person = (
                        people.people()
                        .createContact(
                            body=body,
                            personFields="names,emailAddresses,organizations,userDefined,metadata",
                        )
                        .execute()
                    )
                    if rules_applied:
                        rules_hits += 1
                    created.append(
                        {
                            "email": row["email"],
                            "resource_name": person.get("resourceName"),
                            "first_name": first_name,
                            "last_name": last_name,
                            "organization": org or None,
                            "rules_applied": rules_applied or None,
                        }
                    )
                except Exception as inner:
                    log.error("create contact %s failed: %s", row["email"], inner)
                    failed.append({"email": row["email"], "error": str(inner)})

            rules_result = (
                {"contacts_touched_by_rules": rules_hits}
                if params.apply_rules_after
                else "skipped"
            )

            # 8. Optionally enrich each newly-created contact from their inbound mail.
            enrichment_result: dict | str = "skipped"
            if params.enrich_from_inbox and created:
                try:
                    from tools.enrichment import _enrich_one  # lazy import
                    enriched_summaries: list[dict] = []
                    for c in created:
                        try:
                            summary_row = _enrich_one(
                                email=c["email"],
                                days=params.enrich_days,
                                overwrite=True,
                                conservative_titles=True,
                                dry_run=False,
                            )
                            enriched_summaries.append(summary_row)
                        except Exception as inner:
                            log.warning(
                                "enrich_from_inbox for %s failed: %s", c["email"], inner
                            )
                            enriched_summaries.append(
                                {"email": c["email"], "status": "failed", "error": str(inner)}
                            )
                    enrichment_result = {
                        "enriched_updated": sum(
                            1 for r in enriched_summaries if r.get("status") == "updated"
                        ),
                        "enriched_no_mail_found": sum(
                            1
                            for r in enriched_summaries
                            if r.get("status") == "skipped_no_recent_mail"
                        ),
                        "enriched_no_changes": sum(
                            1
                            for r in enriched_summaries
                            if r.get("status") == "no_changes_needed"
                        ),
                        "sample": enriched_summaries[:10],
                    }
                except Exception as e:
                    log.warning("enrich_from_inbox step failed: %s", e)
                    enrichment_result = {"error": str(e)}

            summary = {
                "query": query,
                "messages_scanned": len(message_ids),
                "unique_candidates": len(plan),
                "created": len(created),
                "failed": len(failed),
                "skipped_existing": params.skip_existing,
                "rules_applied": rules_result,
                "enrichment": enrichment_result,
                "created_sample": created[:20],
                "failed_sample": failed[:20],
            }
            log.info(
                "workflow_create_contacts_from_sent_mail: scanned=%d created=%d failed=%d",
                len(message_ids),
                len(created),
                len(failed),
            )
            return json.dumps(summary, indent=2)
        except Exception as e:
            log.error("workflow_create_contacts_from_sent_mail failed: %s", e)
            return format_error(e)

    # --- Find meeting slot --------------------------------------------------

    @mcp.tool(
        name="workflow_bulk_update_contacts",
        annotations={
            "title": "Patch many contacts with a single dry-run preview + rollback-on-failure",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_bulk_update_contacts(params: BulkUpdateContactsInput) -> str:
        """Apply multiple contact patches with dry-run preview and rollback.

        Pattern (template for future workflow_bulk_*):
          1. Snapshot every target before any write.
          2. If `dry_run=True`, return what WOULD happen without writing.
          3. Apply patches one by one.
          4. If any fail and `rollback_on_partial_failure=True`, restore from snapshots.
          5. Record every successful op + the rollback (if any) to recent_actions.

        Each patch supports: organization, title, notes, custom_fields (dict).
        Set a key to None to clear it. Custom fields with managed CRM keys
        (Last Interaction, Sent/Received tallies) are silently ignored.
        """
        try:
            import recent_actions as _ra
            import crm_stats
            from tools.contacts import _flatten_person
            people = gservices.people()

            # 1. Snapshot every target.
            snapshots: dict[str, dict] = {}
            failed_lookups: list[dict] = []
            for u in params.updates:
                try:
                    person = people.people().get(
                        resourceName=u.resource_name,
                        personFields="names,emailAddresses,organizations,biographies,userDefined,memberships,metadata",
                    ).execute()
                    snapshots[u.resource_name] = person
                except Exception as e:
                    failed_lookups.append({
                        "resource_name": u.resource_name,
                        "error": str(e)[:200],
                    })
            if failed_lookups:
                return json.dumps({
                    "status": "lookup_failed",
                    "failed": failed_lookups,
                    "hint": (
                        "Couldn't fetch some contacts. Check resource_names or "
                        "remove failed entries before re-running."
                    ),
                }, indent=2)

            # 2. Dry-run preview.
            previews: list[dict] = []
            for u in params.updates:
                snap = snapshots[u.resource_name]
                flat = _flatten_person(snap)
                changes: dict = {}
                for k, v in u.patch.items():
                    if k == "custom_fields" and isinstance(v, dict):
                        cleaned = {
                            ck: cv for ck, cv in v.items()
                            if not crm_stats.is_managed_key(ck)
                        }
                        if cleaned:
                            changes["custom_fields"] = cleaned
                    elif k in ("organization", "title", "notes"):
                        if v != flat.get(k):
                            changes[k] = {"from": flat.get(k), "to": v}
                previews.append({
                    "resource_name": u.resource_name,
                    "name": flat.get("name"),
                    "changes": changes,
                })

            if is_dry_run(params.dry_run):
                return json.dumps({
                    "status": "dry_run",
                    "would_update": len(previews),
                    "previews": previews,
                }, indent=2)

            # 3. Apply.
            applied: list[dict] = []
            failed: list[dict] = []
            action_ids: list[str] = []
            for u in params.updates:
                snap = snapshots[u.resource_name]
                etag = snap.get("etag")
                body: dict = {"etag": etag}
                update_fields: list[str] = []
                if "organization" in u.patch or "title" in u.patch:
                    org_block: dict = {}
                    if "organization" in u.patch:
                        org_block["name"] = u.patch["organization"] or ""
                    if "title" in u.patch:
                        org_block["title"] = u.patch["title"] or ""
                    body["organizations"] = [org_block]
                    update_fields.append("organizations")
                if "notes" in u.patch:
                    body["biographies"] = [{
                        "value": u.patch["notes"] or "",
                        "contentType": "TEXT_PLAIN",
                    }]
                    update_fields.append("biographies")
                if "custom_fields" in u.patch and isinstance(u.patch["custom_fields"], dict):
                    cleaned = {
                        ck: str(cv) for ck, cv in u.patch["custom_fields"].items()
                        if not crm_stats.is_managed_key(ck) and cv is not None
                    }
                    existing_ud = snap.get("userDefined") or []
                    by_key = {
                        ux.get("key"): dict(ux) for ux in existing_ud
                        if ux.get("key")
                    }
                    for ck, cv in cleaned.items():
                        by_key[ck] = {"key": ck, "value": cv}
                    body["userDefined"] = list(by_key.values())
                    update_fields.append("userDefined")
                if not update_fields:
                    continue
                try:
                    updated = people.people().updateContact(
                        resourceName=u.resource_name,
                        updatePersonFields=",".join(update_fields),
                        body=body,
                    ).execute()
                    flat_before = _flatten_person(snap)
                    flat_after = _flatten_person(updated)
                    rec_id = _ra.record(
                        tool="workflow_bulk_update_contacts",
                        action="update",
                        target_kind="contact",
                        target_id=u.resource_name,
                        summary=(
                            f"Updated {flat_after.get('name') or u.resource_name}: "
                            f"{', '.join(update_fields)}"
                        ),
                        snapshot_before={
                            "organization": flat_before.get("organization"),
                            "title": flat_before.get("title"),
                            "notes": flat_before.get("notes"),
                            "custom": flat_before.get("custom"),
                        },
                        snapshot_after={
                            "organization": flat_after.get("organization"),
                            "title": flat_after.get("title"),
                            "notes": flat_after.get("notes"),
                            "custom": flat_after.get("custom"),
                        },
                    )
                    action_ids.append(rec_id)
                    applied.append({
                        "resource_name": u.resource_name,
                        "name": flat_after.get("name"),
                        "action_id": rec_id,
                    })
                except Exception as e:
                    failed.append({
                        "resource_name": u.resource_name,
                        "error": str(e)[:300],
                    })
                    if params.rollback_on_partial_failure:
                        # Roll back already-applied changes from snapshots.
                        rollback_results = []
                        for already in applied:
                            rn = already["resource_name"]
                            snap = snapshots[rn]
                            try:
                                # Restore organizations + biographies + userDefined
                                # to their pre-batch state.
                                rollback_body: dict = {"etag": None}
                                # We need a fresh etag — fetch current.
                                fresh = people.people().get(
                                    resourceName=rn,
                                    personFields="metadata",
                                ).execute()
                                rollback_body["etag"] = fresh.get("etag")
                                rollback_body["organizations"] = snap.get("organizations") or [{}]
                                rollback_body["biographies"] = snap.get("biographies") or []
                                rollback_body["userDefined"] = snap.get("userDefined") or []
                                people.people().updateContact(
                                    resourceName=rn,
                                    updatePersonFields="organizations,biographies,userDefined",
                                    body=rollback_body,
                                ).execute()
                                # Record the revert.
                                _ra.record(
                                    tool="workflow_bulk_update_contacts",
                                    action="update",
                                    target_kind="contact",
                                    target_id=rn,
                                    summary=f"Rollback for {rn} after partial failure",
                                    snapshot_before=already.get("snapshot_after"),
                                    snapshot_after=snap,
                                    revert_target_action_id=already["action_id"],
                                )
                                _ra.mark_reverted(already["action_id"], "rollback")
                                rollback_results.append({
                                    "resource_name": rn, "status": "rolled_back",
                                })
                            except Exception as rb_err:
                                rollback_results.append({
                                    "resource_name": rn,
                                    "status": "rollback_failed",
                                    "error": str(rb_err)[:200],
                                })
                        return json.dumps({
                            "status": "rolled_back",
                            "failed_target": u.resource_name,
                            "fail_error": str(e)[:300],
                            "rollback": rollback_results,
                            "hint": "All changes reverted to pre-batch state.",
                        }, indent=2)

            return json.dumps({
                "status": "ok" if not failed else "partial",
                "applied_count": len(applied),
                "failed_count": len(failed),
                "applied": applied,
                "failed": failed,
                "action_ids": action_ids,
                "hint": (
                    "View these later via system_recent_actions. The "
                    "snapshot_before fields can be used to undo individual "
                    "changes."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_bulk_update_contacts failed: %s", e)
            return format_error(e)

    # --- Maps × CRM × Calendar workflows ------------------------------------ #

    @mcp.tool(
        name="workflow_nearby_contacts",
        annotations={
            "title": "Find saved contacts near a location, ranked by distance or recency",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_nearby_contacts(params: NearbyContactsInput) -> str:
        """'I'm in Austin Thu/Fri — who should I see?'

        Geocodes the input location, walks saved contacts, and returns those
        within `radius_km` ranked by distance (default) or recency. Contacts
        with stored lat/lng custom fields are matched instantly; missing ones
        are geocoded on-the-fly unless `require_geocoded=True`.

        Cost reference: ~$0.005 per contact geocoded on-the-fly + ~$0.005 per
        result if `include_travel_time=True`.
        """
        try:
            gmaps = gservices.maps()

            # 1. Anchor: geocode the input location.
            anchor_results = gmaps.geocode(params.location)
            if not anchor_results:
                return json.dumps({"status": "anchor_not_found", "location": params.location}, indent=2)
            anchor_geom = anchor_results[0]["geometry"]["location"]
            anchor = (anchor_geom["lat"], anchor_geom["lng"])
            anchor_label = anchor_results[0].get("formatted_address", params.location)

            # 2. Walk contacts, score each.
            scored: list[dict] = []
            for p in _walk_all_contacts(
                only_groups=params.only_groups, max_contacts=5000,
            ):
                addr_block = _extract_address_block(p)
                latlng = _contact_lat_lng(p)
                if not latlng:
                    if params.require_geocoded:
                        continue
                    formatted = addr_block.get("formatted")
                    if not formatted:
                        continue
                    try:
                        gres = gmaps.geocode(formatted)
                    except Exception:
                        continue
                    if not gres:
                        continue
                    g = gres[0]["geometry"]["location"]
                    latlng = (g["lat"], g["lng"])
                dist_km = _haversine_km(anchor, latlng)
                if dist_km > params.radius_km:
                    continue
                flat = _flatten_person(p)
                custom = flat.get("custom") or {}
                last_interaction = custom.get("Last Interaction")
                scored.append({
                    "resource_name": flat.get("resource_name"),
                    "name": flat.get("name"),
                    "email": flat.get("email"),
                    "organization": flat.get("organization"),
                    "title": flat.get("title"),
                    "address": addr_block.get("formatted"),
                    "city": addr_block.get("city"),
                    "region": addr_block.get("region"),
                    "lat": latlng[0],
                    "lng": latlng[1],
                    "distance_km": round(dist_km, 2),
                    "last_interaction": last_interaction,
                })

            # 3. Sort.
            if params.sort_by == "recency":
                scored.sort(
                    key=lambda r: (r.get("last_interaction") or ""), reverse=True,
                )
            else:
                scored.sort(key=lambda r: r["distance_km"])

            top = scored[: params.limit]

            # 4. Optional travel time.
            if params.include_travel_time and top:
                origins = [params.location]
                destinations = [f"{r['lat']},{r['lng']}" for r in top]
                try:
                    dm = gmaps.distance_matrix(
                        origins=origins, destinations=destinations,
                        mode=params.travel_mode,
                    )
                    rows = (dm.get("rows") or [{}])[0].get("elements") or []
                    for r, el in zip(top, rows):
                        if el.get("status") == "OK":
                            r["travel_minutes"] = round(el["duration"]["value"] / 60, 1)
                            r["travel_distance_km"] = round(el["distance"]["value"] / 1000, 2)
                except Exception as e:
                    log.warning("workflow_nearby_contacts: distance_matrix failed: %s", e)

            return json.dumps({
                "anchor": anchor_label,
                "anchor_lat": anchor[0],
                "anchor_lng": anchor[1],
                "radius_km": params.radius_km,
                "total_in_radius": len(scored),
                "returned": len(top),
                "sort_by": params.sort_by,
                "results": top,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_nearby_contacts failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_geocode_contacts_batch",
        annotations={
            "title": "Geocode every contact's address and store lat/lng custom fields",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_geocode_contacts_batch(params: GeocodeContactsBatchInput) -> str:
        """One-shot pass to geocode every contact's address.

        Stores `lat`, `lng`, `geocoded_at` as userDefined custom fields. Skips
        contacts that already have lat/lng unless `force=True`. Run this once
        before using `workflow_nearby_contacts` etc. for fast spatial queries.

        Cost: ~$0.005 per contact geocoded. 200 contacts ≈ $1.
        """
        try:
            gmaps = gservices.maps()
            import datetime as _dt

            results = {"geocoded": 0, "already_done": 0, "no_address": 0,
                       "geocode_failed": 0, "examples": []}
            processed = 0
            for p in _walk_all_contacts(
                only_groups=params.only_groups, max_contacts=params.max_contacts,
            ):
                processed += 1
                addr_block = _extract_address_block(p)
                addr = addr_block.get("formatted")
                if not addr:
                    results["no_address"] += 1
                    continue
                existing = _contact_lat_lng(p)
                if existing and not params.force:
                    results["already_done"] += 1
                    continue
                try:
                    gres = gmaps.geocode(addr)
                except Exception as e:
                    log.warning("geocode %s failed: %s", addr, e)
                    results["geocode_failed"] += 1
                    continue
                if not gres:
                    results["geocode_failed"] += 1
                    continue
                loc = gres[0]["geometry"]["location"]
                if is_dry_run(params.dry_run):
                    if len(results["examples"]) < 5:
                        results["examples"].append({
                            "name": (p.get("names", [{}])[0] or {}).get("displayName"),
                            "address": addr, "lat": loc["lat"], "lng": loc["lng"],
                        })
                    results["geocoded"] += 1
                    continue
                try:
                    _set_contact_custom_fields(
                        person_resource_name=p["resourceName"],
                        etag=p["etag"],
                        existing_userdefined=p.get("userDefined") or [],
                        updates={
                            "lat": f"{loc['lat']:.6f}",
                            "lng": f"{loc['lng']:.6f}",
                            "geocoded_at": _dt.datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    results["geocoded"] += 1
                except Exception as e:
                    log.warning("update %s failed: %s", p.get("resourceName"), e)
                    results["geocode_failed"] += 1

            return json.dumps({
                "status": "dry_run" if is_dry_run(params.dry_run) else "done",
                "processed": processed,
                **results,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_geocode_contacts_batch failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_address_hygiene_audit",
        annotations={
            "title": "Validate every contact's address and produce a fix-it report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_address_hygiene_audit(params: AddressHygieneAuditInput) -> str:
        """Sweep all contacts, validate addresses via Address Validation API.

        Categorizes each as VALID / SUSPECT (Google inferred components) /
        INVALID, and optionally writes a Google Sheet with the report including
        suggested replacements. Use the resulting Sheet to fix things in bulk.

        Cost: ~$0.017 per contact validated. 200 contacts ≈ $3.40.
        """
        try:
            import datetime as _dt
            import os as _os
            import requests as _requests
            api_key = (
                config.get("google_maps_api_key")
                or _os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
            )
            if not api_key:
                return json.dumps({"status": "maps_not_configured"}, indent=2)

            rows = [["Name", "Email", "Current Address", "Verdict", "Suggested",
                    "Issues", "ResourceName"]]
            counts = {"VALID": 0, "SUSPECT": 0, "INVALID": 0, "NO_ADDRESS": 0}
            processed = 0

            for p in _walk_all_contacts(
                only_groups=params.only_groups, max_contacts=params.max_contacts,
            ):
                processed += 1
                addr_block = _extract_address_block(p)
                addr = addr_block.get("formatted")
                if not addr:
                    counts["NO_ADDRESS"] += 1
                    continue
                payload: dict = {"address": {"addressLines": [addr]}}
                if params.region_code:
                    payload["address"]["regionCode"] = params.region_code
                try:
                    http = _requests.post(
                        f"https://addressvalidation.googleapis.com/v1:validateAddress?key={api_key}",
                        json=payload, timeout=10,
                    )
                    http.raise_for_status()
                    data = http.json().get("result", {})
                except Exception as e:
                    log.warning("validate %s: %s", addr, e)
                    counts["INVALID"] += 1
                    rows.append([
                        (_flatten_person(p).get("name") or ""),
                        (_flatten_person(p).get("email") or ""),
                        addr, "INVALID", "", str(e), p.get("resourceName") or "",
                    ])
                    continue
                verdict = data.get("verdict") or {}
                addr_resp = data.get("address") or {}
                inferred = bool(verdict.get("hasInferredComponents"))
                missing = bool(verdict.get("hasReplacedComponents")) or bool(
                    verdict.get("hasUnconfirmedComponents")
                )
                completeness = (verdict.get("addressComplete") is True)
                if completeness and not (inferred or missing):
                    label = "VALID"
                elif inferred or missing:
                    label = "SUSPECT"
                else:
                    label = "INVALID"
                counts[label] = counts.get(label, 0) + 1
                suggested = addr_resp.get("formattedAddress") or ""
                issues = []
                if inferred:
                    issues.append("inferred_components")
                if missing:
                    issues.append("replaced_or_unconfirmed")
                if not completeness:
                    issues.append("incomplete")
                rows.append([
                    (_flatten_person(p).get("name") or ""),
                    (_flatten_person(p).get("email") or ""),
                    addr, label, suggested, ",".join(issues),
                    p.get("resourceName") or "",
                ])

            sheet_url = None
            sheet_id = None
            if params.write_to_sheet and len(rows) > 1:
                try:
                    sheets = gservices.sheets()
                    title = params.sheet_title or (
                        f"Contact Address Audit - {_dt.date.today().isoformat()}"
                    )
                    created = sheets.spreadsheets().create(
                        body={"properties": {"title": title}}
                    ).execute()
                    sheet_id = created.get("spreadsheetId")
                    sheet_url = created.get("spreadsheetUrl")
                    sheets.spreadsheets().values().update(
                        spreadsheetId=sheet_id, range="A1",
                        valueInputOption="RAW", body={"values": rows},
                    ).execute()
                except Exception as e:
                    log.warning("audit sheet write failed: %s", e)

            return json.dumps({
                "status": "done", "processed": processed, "counts": counts,
                "sheet_url": sheet_url, "sheet_id": sheet_id,
                "row_count": len(rows) - 1,
            }, indent=2)
        except Exception as e:
            log.error("workflow_address_hygiene_audit failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_contact_density_map",
        annotations={
            "title": "Static map of where your contacts are clustered",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_contact_density_map(params: ContactDensityMapInput) -> str:
        """Render a static map showing where your saved contacts live.

        Pulls stored lat/lng from contact custom fields. Run
        `workflow_geocode_contacts_batch` first if your contacts aren't yet
        geocoded.

        Cost: ~$0.002 (one Static Maps call).
        """
        try:
            from tools.maps import _parse_size
            gmaps = gservices.maps()
            markers = []
            counts_by_region: dict[str, int] = {}
            for p in _walk_all_contacts(only_groups=params.only_groups, max_contacts=2000):
                addr_block = _extract_address_block(p)
                if params.region_filter:
                    blob = " ".join([
                        (addr_block.get("city") or ""),
                        (addr_block.get("region") or ""),
                        (addr_block.get("formatted") or ""),
                    ]).lower()
                    if params.region_filter.lower() not in blob:
                        continue
                latlng = _contact_lat_lng(p)
                if not latlng:
                    continue
                if len(markers) < params.max_markers:
                    markers.append(f"{latlng[0]:.6f},{latlng[1]:.6f}")
                key = (addr_block.get("region") or addr_block.get("country") or "Unknown")
                counts_by_region[key] = counts_by_region.get(key, 0) + 1
            if not markers:
                return json.dumps({
                    "status": "no_geocoded_contacts",
                    "hint": "Run workflow_geocode_contacts_batch first.",
                }, indent=2)
            chunks = gmaps.static_map(
                size=_parse_size(params.size),
                maptype=params.map_type,
                markers=markers,
            )
            map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks
            saved_to = None
            b64 = None
            if params.save_to_path:
                Path = __import__("pathlib").Path
                Path(params.save_to_path).write_bytes(map_bytes)
                saved_to = params.save_to_path
            else:
                import base64 as _b64
                b64 = _b64.b64encode(map_bytes).decode("ascii")
            return json.dumps({
                "status": "ok",
                "marker_count": len(markers),
                "counts_by_region": counts_by_region,
                "map_size_kb": round(len(map_bytes) / 1024),
                "saved_to": saved_to,
                "image_base64": b64,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_contact_density_map failed: %s", e)
            return format_error(e)

