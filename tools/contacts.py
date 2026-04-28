"""Google Contacts tools (via People API) + CRM workflows.

Covers:
    - Search / list / get / create / update / delete contacts
    - Notes (via `biographies`) and custom key/value fields (via `userDefined`)
    - Contact groups (create / add / remove / list members) for segmentation
    - Interaction history (last + recent Gmail threads to/from a contact)

The `_flatten_person` helper produces the dict shape used by mail-merge
templates — `first_name`, `last_name`, `email`, `organization`, `title`,
`custom.<key>` for userDefined fields, plus `full_name` / `name`.
"""

from __future__ import annotations

import gservices

import base64
import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import crm_stats
import rules as rules_mod
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log


def _service():
    return gservices.people()


def _gmail():
    return gservices.gmail()


# Fields we pull by default. Broader than v1 because mail-merge wants the
# custom fields, notes, and group memberships.
_PERSON_FIELDS = (
    "names,emailAddresses,phoneNumbers,organizations,metadata,biographies,"
    "userDefined,memberships,addresses,birthdays,urls"
)


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class SearchContactsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="Substring to match against name/email/org.")
    include_other_contacts: bool = Field(
        default=True,
        description="Also search 'Other contacts' (auto-saved addresses Gmail has seen).",
    )
    limit: int = Field(default=20, ge=1, le=50)


class ListContactsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    limit: int = Field(default=100, ge=1, le=1000)
    page_token: Optional[str] = Field(default=None)


class GetContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(
        ...,
        description="People API resource name, e.g. 'people/c12345'. Get from contacts_search / contacts_list.",
    )


class CreateContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    first_name: Optional[str] = Field(default=None)
    last_name: Optional[str] = Field(default=None)
    emails: Optional[list[str]] = Field(default=None, description="Email addresses.")
    phones: Optional[list[str]] = Field(default=None, description="Phone numbers.")
    organization: Optional[str] = Field(default=None, description="Company/organization name.")
    title: Optional[str] = Field(default=None, description="Job title.")
    notes: Optional[str] = Field(default=None, description="Free-text note (maps to People API 'biographies').")
    custom_fields: Optional[dict[str, str]] = Field(
        default=None,
        description="Key/value tags stored as 'userDefined' (e.g. {'stage': 'prospect', 'tier': 'enterprise'}).",
    )
    group_resource_names: Optional[list[str]] = Field(
        default=None, description="Contact groups to add this contact to on creation."
    )


class UpdateContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(...)
    first_name: Optional[str] = Field(default=None)
    last_name: Optional[str] = Field(default=None)
    emails: Optional[list[str]] = Field(default=None)
    phones: Optional[list[str]] = Field(default=None)
    organization: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)


class DeleteContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(...)
    dry_run: Optional[bool] = Field(default=None)


class AddNoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(...)
    note: str = Field(..., description="Note text. Appended to the existing biography with a timestamp header.")
    timestamp: bool = Field(
        default=True,
        description="Prepend a dated header to the new note (e.g. '[2026-04-23] ...').",
    )


class SetCustomFieldInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(...)
    key: str = Field(..., description="Custom-field key (e.g. 'stage', 'tier', 'last_qbr').")
    value: str = Field(..., description="Custom-field value.")


class ListGroupsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGroupInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Group name (e.g. 'Q2 Prospects', 'Active Clients').")


class ModifyGroupMembersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group_resource_name: str = Field(..., description="e.g. 'contactGroups/123abc'.")
    contact_resource_names: list[str] = Field(
        ..., min_length=1, description="People resource names to add or remove."
    )


class ListGroupMembersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group_resource_name: str = Field(...)
    limit: int = Field(default=100, ge=1, le=1000)


class LastInteractionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(..., description="Email address to look up in Gmail.")


class RecentInteractionsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(...)
    limit: int = Field(default=10, ge=1, le=50)


class RefreshStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(..., description="People resource name, e.g. 'people/c123'.")


class RefreshAllStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    limit: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="Max contacts to process in this run. Paginate by calling again.",
    )
    only_with_email: bool = Field(
        default=True,
        description="Skip contacts without a primary email (they get empty stats anyway).",
    )
    dry_run: Optional[bool] = Field(default=None)


class ApplyRulesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: Optional[str] = Field(
        default=None,
        description="Apply rules to one contact by resource_name. Omit to sweep all contacts.",
    )
    limit: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="When sweeping all contacts, max to process in this run.",
    )
    dry_run: Optional[bool] = Field(default=None)


class ExportCsvInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, extra="forbid", populate_by_name=True
    )

    path: str = Field(
        ...,
        alias="output_path",
        description="Absolute path for the CSV output file. Alias `output_path` is also accepted.",
    )
    include_managed: bool = Field(
        default=True,
        description="Include the managed CRM fields (Last Interaction, Sent/Received last N) as columns.",
    )


class ImportCsvInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(..., description="Absolute path to the CSV to import.")
    update_existing: bool = Field(
        default=True,
        description=(
            "If True and a contact with the same primary email already exists, update it. "
            "If False, skip existing contacts."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _flatten_person(p: dict) -> dict:
    """Rich flattening for CRM/mail-merge use.

    Returns a dict with stable keys: resource_name, name, full_name, first_name,
    last_name, email (first), emails, phones, organization, title, notes,
    custom (dict of userDefined key→value), groups (list of resource_names).
    """
    names = (p.get("names", [{}])[0]) if p.get("names") else {}
    org = (p.get("organizations", [{}])[0]) if p.get("organizations") else {}
    emails = [e.get("value") for e in p.get("emailAddresses", []) if e.get("value")]
    notes_parts = [b.get("value", "") for b in p.get("biographies", []) if b.get("value")]
    custom = {
        u.get("key"): u.get("value")
        for u in p.get("userDefined", [])
        if u.get("key")
    }
    memberships = [
        m.get("contactGroupMembership", {}).get("contactGroupResourceName")
        for m in p.get("memberships", [])
        if m.get("contactGroupMembership")
    ]
    return {
        "resource_name": p.get("resourceName"),
        "etag": p.get("etag"),  # needed for updates — People API requires it
        "name": names.get("displayName"),
        "full_name": names.get("displayName"),
        "first_name": names.get("givenName"),
        "last_name": names.get("familyName"),
        "email": emails[0] if emails else None,
        "emails": emails,
        "phones": [ph.get("value") for ph in p.get("phoneNumbers", []) if ph.get("value")],
        "organization": org.get("name"),
        "title": org.get("title"),
        "notes": "\n\n".join(notes_parts) if notes_parts else None,
        "custom": custom,
        "groups": [g for g in memberships if g],
    }


def _build_person_body(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    emails: Optional[list[str]] = None,
    phones: Optional[list[str]] = None,
    organization: Optional[str] = None,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    custom_fields: Optional[dict[str, str]] = None,
    group_memberships: Optional[list[str]] = None,
) -> dict:
    """Translate flat args into the People API 'person' structure."""
    body: dict[str, Any] = {}
    if first_name is not None or last_name is not None:
        name: dict[str, str] = {}
        if first_name is not None:
            name["givenName"] = first_name
        if last_name is not None:
            name["familyName"] = last_name
        body["names"] = [name]
    if emails is not None:
        body["emailAddresses"] = [{"value": e} for e in emails]
    if phones is not None:
        body["phoneNumbers"] = [{"value": p} for p in phones]
    if organization is not None or title is not None:
        org: dict[str, str] = {}
        if organization is not None:
            org["name"] = organization
        if title is not None:
            org["title"] = title
        body["organizations"] = [org]
    if notes is not None:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
    if custom_fields is not None:
        body["userDefined"] = [{"key": k, "value": v} for k, v in custom_fields.items()]
    if group_memberships is not None:
        body["memberships"] = [
            {"contactGroupMembership": {"contactGroupResourceName": rn}}
            for rn in group_memberships
        ]
    return body


def _update_person_mask(body: dict) -> str:
    """Compute the `updatePersonFields` mask from which keys appear in body."""
    mapping = {
        "names": "names",
        "emailAddresses": "emailAddresses",
        "phoneNumbers": "phoneNumbers",
        "organizations": "organizations",
        "biographies": "biographies",
        "userDefined": "userDefined",
        "memberships": "memberships",
    }
    return ",".join(mapping[k] for k in body.keys() if k in mapping)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="contacts_search",
        annotations={
            "title": "Search Google Contacts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_search(params: SearchContactsInput) -> str:
        """Search your Google Contacts by name/email/org substring.

        Saved contacts are scanned via `connections().list()` and filtered
        locally — this avoids the People API `searchContacts` indexing lag,
        which can miss contacts created in the last few minutes and which
        sometimes prefers Other Contacts entries over the saved version.

        Also searches 'Other contacts' (people you've emailed but haven't saved)
        when `include_other_contacts` is True. Other Contacts entries are
        deduped against saved ones by primary email — the saved version wins.
        """
        try:
            svc = _service()
            query_lower = params.query.lower()
            results: list[dict] = []
            seen_emails: set[str] = set()

            # 1. Walk saved contacts via connections().list() — reliable + fresh.
            page_token = None
            scanned = 0
            MAX_SCAN = 5000  # safety cap for huge address books
            while scanned < MAX_SCAN and len(results) < params.limit:
                kwargs: dict = {
                    "resourceName": "people/me",
                    "personFields": _PERSON_FIELDS,
                    "pageSize": 1000,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = svc.people().connections().list(**kwargs).execute()
                for p in resp.get("connections", []) or []:
                    scanned += 1
                    flat = _flatten_person(p)
                    blob = " ".join(
                        filter(
                            None,
                            [
                                flat.get("name", ""),
                                flat.get("first_name", ""),
                                flat.get("last_name", ""),
                                flat.get("email", ""),
                                flat.get("organization", ""),
                                flat.get("title", ""),
                            ],
                        )
                    ).lower()
                    if query_lower in blob:
                        results.append(flat)
                        primary_email = (flat.get("email") or "").lower()
                        if primary_email:
                            seen_emails.add(primary_email)
                        if len(results) >= params.limit:
                            break
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            # 2. Supplement with Other Contacts (deduped by primary email).
            if params.include_other_contacts and len(results) < params.limit:
                try:
                    svc.otherContacts().search(
                        query="", readMask="names,emailAddresses"
                    ).execute()
                    other = (
                        svc.otherContacts()
                        .search(
                            query=params.query,
                            readMask="names,emailAddresses",
                            pageSize=params.limit,
                        )
                        .execute()
                    )
                    for r in other.get("results", []):
                        flat = _flatten_person(r["person"])
                        primary_email = (flat.get("email") or "").lower()
                        if primary_email and primary_email in seen_emails:
                            continue  # already have the saved version
                        results.append(flat)
                        if primary_email:
                            seen_emails.add(primary_email)
                        if len(results) >= params.limit:
                            break
                except Exception as e:
                    log.warning("contacts_search: otherContacts step failed: %s", e)

            return json.dumps(
                {"count": len(results), "contacts": results[: params.limit]},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_list",
        annotations={
            "title": "List Google Contacts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_list(params: ListContactsInput) -> str:
        """List the authenticated user's saved contacts (paged)."""
        try:
            kwargs = {
                "resourceName": "people/me",
                "personFields": _PERSON_FIELDS,
                "pageSize": params.limit,
            }
            if params.page_token:
                kwargs["pageToken"] = params.page_token
            resp = _service().people().connections().list(**kwargs).execute()
            out = [_flatten_person(p) for p in resp.get("connections", [])]
            return json.dumps(
                {
                    "count": len(out),
                    "contacts": out,
                    "next_page_token": resp.get("nextPageToken"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_get",
        annotations={
            "title": "Get a Google Contact by resource name",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_get(params: GetContactInput) -> str:
        """Fetch a contact's full record (including notes, custom fields, group memberships)."""
        try:
            person = (
                _service()
                .people()
                .get(resourceName=params.resource_name, personFields=_PERSON_FIELDS)
                .execute()
            )
            return json.dumps(_flatten_person(person), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_create",
        annotations={
            "title": "Create a Google Contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def contacts_create(params: CreateContactInput) -> str:
        """Create a new saved contact with optional notes, custom fields, and group memberships.

        Also computes and persists the three managed CRM fields
        ('Last Interaction', 'Sent, last 60', 'Received, last 60') using the
        primary email address. These are written on top of whatever custom
        fields you supply — if you tried to supply any of the three managed
        keys, those values are stripped and replaced with the computed ones.
        """
        try:
            # Sanitize caller-supplied custom_fields: managed keys are off-limits.
            safe_custom = None
            if params.custom_fields:
                blocked = [k for k in params.custom_fields if crm_stats.is_managed_key(k)]
                if blocked:
                    log.info(
                        "contacts_create: dropping caller writes to managed keys %s", blocked
                    )
                safe_custom = {
                    k: v
                    for k, v in params.custom_fields.items()
                    if not crm_stats.is_managed_key(k)
                }

            # Auto-tagging rules: fill in blanks based on email domain.
            primary_email_early = (params.emails or [None])[0]
            rule_top, rule_custom, rules_applied = rules_mod.apply_rules(
                primary_email_early,
                existing_fields={
                    "first_name": params.first_name,
                    "last_name": params.last_name,
                    "organization": params.organization,
                    "title": params.title,
                },
                existing_custom=safe_custom or {},
            )
            if rules_applied:
                log.info("contacts_create: applied rules %s for %s", rules_applied, primary_email_early)

            merged_custom = dict(safe_custom or {})
            merged_custom.update(rule_custom)

            body = _build_person_body(
                first_name=params.first_name or rule_top.get("first_name"),
                last_name=params.last_name or rule_top.get("last_name"),
                emails=params.emails,
                phones=params.phones,
                organization=params.organization or rule_top.get("organization"),
                title=params.title or rule_top.get("title"),
                notes=params.notes,
                custom_fields=merged_custom or None,
                group_memberships=params.group_resource_names,
            )
            created = _service().people().createContact(body=body).execute()

            # Seed the managed fields from Gmail activity.
            primary_email = (params.emails or [None])[0]
            try:
                created = crm_stats.apply_stats_to_contact(
                    created["resourceName"], primary_email
                )
            except Exception as stats_exc:
                log.warning(
                    "contacts_create: stats seeding failed for %s: %s",
                    created.get("resourceName"),
                    stats_exc,
                )

            return json.dumps(_flatten_person(created), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_update",
        annotations={
            "title": "Update a Google Contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_update(params: UpdateContactInput) -> str:
        """Update top-level fields (name, emails, phones, org/title).

        Also refreshes the three managed CRM fields ('Last Interaction',
        'Sent, last 60', 'Received, last 60') against the contact's primary
        email — whether you changed the email or not.

        For notes use contacts_add_note. For tags use contacts_set_custom_field.
        For group membership use contacts_add_to_group / contacts_remove_from_group.
        """
        try:
            svc = _service()
            existing = svc.people().get(
                resourceName=params.resource_name,
                personFields="metadata,emailAddresses",
            ).execute()
            body = _build_person_body(
                first_name=params.first_name,
                last_name=params.last_name,
                emails=params.emails,
                phones=params.phones,
                organization=params.organization,
                title=params.title,
            )
            if not body:
                return "Error: nothing to update — provide at least one field."
            body["etag"] = existing["etag"]
            updated = (
                svc.people()
                .updateContact(
                    resourceName=params.resource_name,
                    updatePersonFields=_update_person_mask(body),
                    body=body,
                )
                .execute()
            )

            # Refresh managed stats against the post-update primary email.
            primary_email = None
            if params.emails:
                primary_email = params.emails[0]
            else:
                addrs = existing.get("emailAddresses") or []
                if addrs:
                    primary_email = addrs[0].get("value")
            try:
                updated = crm_stats.apply_stats_to_contact(
                    params.resource_name, primary_email
                )
            except Exception as stats_exc:
                log.warning(
                    "contacts_update: stats refresh failed for %s: %s",
                    params.resource_name,
                    stats_exc,
                )

            return json.dumps(_flatten_person(updated), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_delete",
        annotations={
            "title": "Delete a Google Contact",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_delete(params: DeleteContactInput) -> str:
        """Delete a contact. This cannot be undone via the API."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "contacts_delete", {"resource_name": params.resource_name}
                )
            _service().people().deleteContact(resourceName=params.resource_name).execute()
            return json.dumps({"status": "deleted", "resource_name": params.resource_name})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_add_note",
        annotations={
            "title": "Append a note to a contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def contacts_add_note(params: AddNoteInput) -> str:
        """Append a timestamped note to a contact's biography.

        People API stores notes as a free-text 'biography'. This tool reads the
        existing note, prepends a timestamp, and appends the new content. So
        adding a note never overwrites earlier notes.
        """
        try:
            import datetime as _dt

            svc = _service()
            existing = svc.people().get(
                resourceName=params.resource_name, personFields="biographies,metadata"
            ).execute()
            bios = existing.get("biographies", [])
            prev = bios[0].get("value", "") if bios else ""
            header = (
                f"[{_dt.date.today().isoformat()}] "
                if params.timestamp
                else ""
            )
            new_note = f"{header}{params.note}"
            combined = (prev + "\n\n" + new_note).strip() if prev else new_note

            body = {
                "etag": existing["etag"],
                "biographies": [{"value": combined, "contentType": "TEXT_PLAIN"}],
            }
            updated = (
                svc.people()
                .updateContact(
                    resourceName=params.resource_name,
                    updatePersonFields="biographies",
                    body=body,
                )
                .execute()
            )
            return json.dumps(
                {"status": "note_added", "resource_name": updated["resourceName"]}, indent=2
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_set_custom_field",
        annotations={
            "title": "Set a custom key/value field on a contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_set_custom_field(params: SetCustomFieldInput) -> str:
        """Set a userDefined key/value on a contact.

        Useful for tagging (e.g. key='stage', value='prospect'; key='tier', value='enterprise').
        Replaces the value if the key already exists; keeps other keys intact.

        Managed keys ('Last Interaction', 'Sent, last 60', 'Received, last 60')
        are read-only and cannot be set through this tool. They auto-refresh
        on contact create/update or via contacts_refresh_crm_stats.
        """
        try:
            if crm_stats.is_managed_key(params.key):
                return (
                    f"Error: '{params.key}' is a managed field and cannot be set manually. "
                    f"It auto-updates from Gmail. Use contacts_refresh_crm_stats "
                    f"to force a refresh."
                )

            svc = _service()
            existing = svc.people().get(
                resourceName=params.resource_name,
                personFields="userDefined,metadata",
            ).execute()
            current = existing.get("userDefined", []) or []
            merged = [u for u in current if u.get("key") != params.key]
            merged.append({"key": params.key, "value": params.value})
            body = {"etag": existing["etag"], "userDefined": merged}
            updated = (
                svc.people()
                .updateContact(
                    resourceName=params.resource_name,
                    updatePersonFields="userDefined",
                    body=body,
                )
                .execute()
            )
            return json.dumps(
                {
                    "status": "ok",
                    "resource_name": updated["resourceName"],
                    "key": params.key,
                    "value": params.value,
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    # --- Groups ---------------------------------------------------------------

    @mcp.tool(
        name="contacts_list_groups",
        annotations={
            "title": "List contact groups",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_list_groups(params: ListGroupsInput) -> str:
        """List all contact groups (built-in + user-created)."""
        try:
            resp = _service().contactGroups().list(pageSize=200).execute()
            groups = [
                {
                    "resource_name": g["resourceName"],
                    "name": g.get("name") or g.get("formattedName"),
                    "group_type": g.get("groupType"),
                    "member_count": g.get("memberCount", 0),
                }
                for g in resp.get("contactGroups", [])
            ]
            return json.dumps({"count": len(groups), "groups": groups}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_create_group",
        annotations={
            "title": "Create a contact group",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def contacts_create_group(params: CreateGroupInput) -> str:
        """Create a new contact group for segmentation (e.g. 'Q2 Prospects')."""
        try:
            created = (
                _service()
                .contactGroups()
                .create(body={"contactGroup": {"name": params.name}})
                .execute()
            )
            return json.dumps(
                {
                    "resource_name": created["resourceName"],
                    "name": created["name"],
                    "group_type": created.get("groupType"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_add_to_group",
        annotations={
            "title": "Add contacts to a group",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_add_to_group(params: ModifyGroupMembersInput) -> str:
        """Add one or more contacts to a contact group."""
        try:
            resp = (
                _service()
                .contactGroups()
                .members()
                .modify(
                    resourceName=params.group_resource_name,
                    body={"resourceNamesToAdd": params.contact_resource_names},
                )
                .execute()
            )
            return json.dumps(resp, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_remove_from_group",
        annotations={
            "title": "Remove contacts from a group",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_remove_from_group(params: ModifyGroupMembersInput) -> str:
        """Remove one or more contacts from a contact group."""
        try:
            resp = (
                _service()
                .contactGroups()
                .members()
                .modify(
                    resourceName=params.group_resource_name,
                    body={"resourceNamesToRemove": params.contact_resource_names},
                )
                .execute()
            )
            return json.dumps(resp, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_list_group_members",
        annotations={
            "title": "List members of a contact group",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_list_group_members(params: ListGroupMembersInput) -> str:
        """Return the contacts in a group as flat records (ready for mail-merge)."""
        try:
            svc = _service()
            grp = (
                svc.contactGroups()
                .get(
                    resourceName=params.group_resource_name,
                    maxMembers=params.limit,
                )
                .execute()
            )
            member_names = grp.get("memberResourceNames", []) or []
            if not member_names:
                return json.dumps({"count": 0, "contacts": []}, indent=2)

            # Batch-get the members' full records.
            batch = (
                svc.people()
                .getBatchGet(resourceNames=member_names, personFields=_PERSON_FIELDS)
                .execute()
            )
            out = [_flatten_person(r["person"]) for r in batch.get("responses", []) if r.get("person")]
            return json.dumps({"count": len(out), "contacts": out}, indent=2)
        except Exception as e:
            return format_error(e)

    # --- Interaction history --------------------------------------------------

    @mcp.tool(
        name="contacts_last_interaction",
        annotations={
            "title": "Find the last Gmail message to/from a contact",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_last_interaction(params: LastInteractionInput) -> str:
        """Look up the most recent Gmail message between you and the given email address."""
        try:
            q = f"(from:{params.email} OR to:{params.email})"
            gmail = _gmail()
            resp = gmail.users().messages().list(userId="me", q=q, maxResults=1).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            if not ids:
                return json.dumps({"email": params.email, "found": False})
            m = (
                gmail.users()
                .messages()
                .get(
                    userId="me",
                    id=ids[0],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            return json.dumps(
                {
                    "email": params.email,
                    "found": True,
                    "message_id": m["id"],
                    "thread_id": m["threadId"],
                    "from": headers.get("From"),
                    "to": headers.get("To"),
                    "subject": headers.get("Subject"),
                    "date": headers.get("Date"),
                    "snippet": m.get("snippet", ""),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_recent_interactions",
        annotations={
            "title": "List recent Gmail messages with a contact",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_recent_interactions(params: RecentInteractionsInput) -> str:
        """Return the N most recent messages (either direction) between you and the given email."""
        try:
            q = f"(from:{params.email} OR to:{params.email})"
            gmail = _gmail()
            resp = gmail.users().messages().list(userId="me", q=q, maxResults=params.limit).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            out = []
            for mid in ids:
                m = (
                    gmail.users()
                    .messages()
                    .get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["From", "To", "Subject", "Date"],
                    )
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
                out.append(
                    {
                        "id": mid,
                        "thread_id": m["threadId"],
                        "from": headers.get("From"),
                        "to": headers.get("To"),
                        "subject": headers.get("Subject"),
                        "date": headers.get("Date"),
                        "snippet": m.get("snippet", ""),
                    }
                )
            return json.dumps({"email": params.email, "count": len(out), "messages": out}, indent=2)
        except Exception as e:
            return format_error(e)

    # --- Managed CRM fields (Last Interaction / Sent 60 / Received 60) -------

    @mcp.tool(
        name="contacts_refresh_crm_stats",
        annotations={
            "title": "Refresh managed CRM fields on one contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_refresh_crm_stats(params: RefreshStatsInput) -> str:
        """Recompute 'Last Interaction', 'Sent, last 60', 'Received, last 60' for one contact.

        Uses the contact's primary email address to query Gmail, then writes the
        three managed fields into the contact's userDefined (preserving your own
        tags). Other userDefined entries are kept intact.
        """
        try:
            svc = _service()
            person = svc.people().get(
                resourceName=params.resource_name,
                personFields="emailAddresses,metadata",
            ).execute()
            addrs = person.get("emailAddresses") or []
            primary_email = addrs[0].get("value") if addrs else None

            updated = crm_stats.apply_stats_to_contact(
                params.resource_name, primary_email
            )
            flat = _flatten_person(updated)
            last_key, sent_key, received_key = crm_stats.current_managed_keys()
            return json.dumps(
                {
                    "resource_name": params.resource_name,
                    "email": primary_email,
                    last_key: flat["custom"].get(last_key),
                    sent_key: flat["custom"].get(sent_key),
                    received_key: flat["custom"].get(received_key),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_refresh_all_crm_stats",
        annotations={
            "title": "Refresh managed CRM fields across all contacts",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_refresh_all_crm_stats(params: RefreshAllStatsInput) -> str:
        """Recompute managed CRM fields for up to `limit` contacts.

        Iterates over your saved contacts, recomputes 'Last Interaction' and
        the two 60-day tallies from Gmail, and writes them back. Skips contacts
        without an email if `only_with_email` (default).

        This is the recommended way to keep managed fields current between
        normal create/update events. Consider scheduling this daily via Cowork
        scheduled tasks or a cron job running `python refresh_stats.py` (see
        README).

        Partial failures don't abort — per-contact status is returned.
        """
        try:
            svc = _service()

            # Fetch up to `limit` contacts.
            kwargs = {
                "resourceName": "people/me",
                "personFields": "names,emailAddresses,metadata",
                "pageSize": min(params.limit, 1000),
            }
            resp = svc.people().connections().list(**kwargs).execute()
            people = resp.get("connections", []) or []

            if is_dry_run(params.dry_run):
                candidates = [
                    {
                        "resource_name": p.get("resourceName"),
                        "name": (p.get("names", [{}])[0] or {}).get("displayName"),
                        "email": (p.get("emailAddresses", [{}])[0] or {}).get("value")
                        if p.get("emailAddresses") else None,
                    }
                    for p in people[: params.limit]
                ]
                if params.only_with_email:
                    candidates = [c for c in candidates if c["email"]]
                return dry_run_preview(
                    "contacts_refresh_all_crm_stats",
                    {"would_refresh": len(candidates), "sample": candidates[:10]},
                )

            results = []
            batch_input: list[tuple[str, str]] = []
            for p in people[: params.limit]:
                resource_name = p.get("resourceName")
                addrs = p.get("emailAddresses") or []
                email = addrs[0].get("value") if addrs else None
                if params.only_with_email and not email:
                    results.append({"resource_name": resource_name, "status": "skipped_no_email"})
                    continue
                batch_input.append((resource_name, email or ""))

            # Batch Gmail lookups + sequential People API writes (People has no batch update).
            batch_results = crm_stats.apply_stats_batch(batch_input)
            for resource_name, email in batch_input:
                r = batch_results.get(resource_name) or {}
                results.append(
                    {
                        "resource_name": resource_name,
                        "email": email,
                        "status": r.get("status", "failed"),
                        **({"error": r["error"]} if r.get("error") else {}),
                    }
                )

            refreshed = sum(1 for r in results if r["status"] == "refreshed")
            failed = sum(1 for r in results if r["status"] == "failed")
            skipped = sum(1 for r in results if r["status"] == "skipped_no_email")
            return json.dumps(
                {
                    "total": len(results),
                    "refreshed": refreshed,
                    "failed": failed,
                    "skipped_no_email": skipped,
                    "next_page_token": resp.get("nextPageToken"),
                    "results": results,
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    # --- Auto-tagging rules ---------------------------------------------------

    @mcp.tool(
        name="contacts_apply_rules",
        annotations={
            "title": "Apply auto-tagging rules to contacts",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_apply_rules(params: ApplyRulesInput) -> str:
        """Evaluate rules.json against one or all contacts and fill in blanks.

        Rules only fill in empty fields — nothing existing is overwritten. Useful
        when you update rules.json and want existing contacts to pick up the
        new fields, or after importing a batch of contacts from CSV.
        """
        try:
            rules_mod.reload()
            svc = _service()

            def _apply_one(resource_name: str) -> dict:
                person = svc.people().get(
                    resourceName=resource_name,
                    personFields=_PERSON_FIELDS,
                ).execute()
                flat = _flatten_person(person)
                primary_email = flat.get("email")
                rule_top, rule_custom, applied = rules_mod.apply_rules(
                    primary_email,
                    existing_fields={
                        "first_name": flat.get("first_name"),
                        "last_name": flat.get("last_name"),
                        "organization": flat.get("organization"),
                        "title": flat.get("title"),
                    },
                    existing_custom=flat.get("custom") or {},
                )
                if not applied:
                    return {
                        "resource_name": resource_name,
                        "email": primary_email,
                        "status": "no_matching_rule",
                    }

                update_body: dict = {"etag": person["etag"]}
                mask_parts: list[str] = []

                if rule_top.get("first_name") or rule_top.get("last_name"):
                    names = person.get("names", [{}])[0] if person.get("names") else {}
                    new_names = {
                        "givenName": rule_top.get("first_name") or names.get("givenName"),
                        "familyName": rule_top.get("last_name") or names.get("familyName"),
                    }
                    update_body["names"] = [new_names]
                    mask_parts.append("names")
                if rule_top.get("organization") or rule_top.get("title"):
                    org = person.get("organizations", [{}])[0] if person.get("organizations") else {}
                    new_org = {
                        "name": rule_top.get("organization") or org.get("name"),
                        "title": rule_top.get("title") or org.get("title"),
                    }
                    update_body["organizations"] = [new_org]
                    mask_parts.append("organizations")
                if rule_custom:
                    existing_user = person.get("userDefined", []) or []
                    merged = list(existing_user)
                    existing_keys = {u.get("key") for u in existing_user}
                    for k, v in rule_custom.items():
                        if k not in existing_keys:
                            merged.append({"key": k, "value": v})
                    update_body["userDefined"] = merged
                    mask_parts.append("userDefined")

                if not mask_parts:
                    return {
                        "resource_name": resource_name,
                        "email": primary_email,
                        "status": "rules_matched_but_all_fields_filled",
                    }

                if is_dry_run(params.dry_run):
                    return {
                        "resource_name": resource_name,
                        "email": primary_email,
                        "would_update": mask_parts,
                        "rules": applied,
                    }

                svc.people().updateContact(
                    resourceName=resource_name,
                    updatePersonFields=",".join(mask_parts),
                    body=update_body,
                ).execute()
                return {
                    "resource_name": resource_name,
                    "email": primary_email,
                    "status": "updated",
                    "rules_applied": applied,
                    "fields_updated": mask_parts,
                }

            if params.resource_name:
                return json.dumps(_apply_one(params.resource_name), indent=2)

            # Sweep all contacts.
            resp = (
                svc.people()
                .connections()
                .list(
                    resourceName="people/me",
                    personFields="metadata,emailAddresses",
                    pageSize=min(params.limit, 1000),
                )
                .execute()
            )
            people = resp.get("connections", []) or []
            results = []
            for p in people[: params.limit]:
                try:
                    results.append(_apply_one(p["resourceName"]))
                except Exception as inner:
                    results.append(
                        {
                            "resource_name": p.get("resourceName"),
                            "status": "failed",
                            "error": str(inner),
                        }
                    )
            return json.dumps(
                {"total": len(results), "results": results, "next_page_token": resp.get("nextPageToken")},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    # --- CSV import/export ---------------------------------------------------

    @mcp.tool(
        name="contacts_export_csv",
        annotations={
            "title": "Export contacts to CSV",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def contacts_export_csv(params: ExportCsvInput) -> str:
        """Write all saved contacts to a CSV file.

        Columns: resource_name, first_name, last_name, email, phone, organization,
        title, notes, groups (semicolon-joined), plus one column per discovered
        custom field (prefixed `custom.<key>`). Managed CRM fields are included
        unless `include_managed=false`.
        """
        try:
            import csv as _csv
            from pathlib import Path as _Path

            svc = _service()
            all_flat: list[dict] = []
            all_custom_keys: set[str] = set()
            page_token = None
            while True:
                kwargs = {
                    "resourceName": "people/me",
                    "personFields": _PERSON_FIELDS,
                    "pageSize": 1000,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = svc.people().connections().list(**kwargs).execute()
                for p in resp.get("connections", []) or []:
                    flat = _flatten_person(p)
                    all_flat.append(flat)
                    for k in (flat.get("custom") or {}):
                        if not params.include_managed and crm_stats.is_managed_key(k):
                            continue
                        all_custom_keys.add(k)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            custom_cols = sorted(all_custom_keys)
            base_cols = [
                "resource_name",
                "first_name",
                "last_name",
                "email",
                "phone",
                "organization",
                "title",
                "notes",
                "groups",
            ]
            header = base_cols + [f"custom.{k}" for k in custom_cols]

            dest = _Path(params.path).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("w", newline="", encoding="utf-8") as fh:
                writer = _csv.writer(fh)
                writer.writerow(header)
                for flat in all_flat:
                    phones = flat.get("phones") or []
                    row = [
                        flat.get("resource_name") or "",
                        flat.get("first_name") or "",
                        flat.get("last_name") or "",
                        flat.get("email") or "",
                        phones[0] if phones else "",
                        flat.get("organization") or "",
                        flat.get("title") or "",
                        (flat.get("notes") or "").replace("\n", "\\n"),
                        ";".join(flat.get("groups") or []),
                    ]
                    custom = flat.get("custom") or {}
                    for k in custom_cols:
                        row.append(custom.get(k, ""))
                    writer.writerow(row)

            return json.dumps(
                {
                    "status": "exported",
                    "path": str(dest),
                    "rows": len(all_flat),
                    "custom_columns": custom_cols,
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="contacts_import_csv",
        annotations={
            "title": "Import contacts from CSV",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def contacts_import_csv(params: ImportCsvInput) -> str:
        """Create or update contacts from a CSV file.

        Expected columns (subset OK): first_name, last_name, email, phone,
        organization, title, notes, and any `custom.<key>` columns for tags.
        If `email` is empty for a row, that row is skipped.

        Matching rule: if `update_existing` is true, a row whose email matches
        an existing contact's primary email triggers an update instead of a
        create. Managed keys in custom.* columns are ignored.
        """
        try:
            import csv as _csv
            from pathlib import Path as _Path

            src = _Path(params.path).expanduser()
            if not src.is_file():
                return f"Error: CSV not found at {src}"

            # Build an email → resource_name index for matching.
            svc = _service()
            existing_by_email: dict[str, str] = {}
            if params.update_existing:
                page_token = None
                while True:
                    kwargs = {
                        "resourceName": "people/me",
                        "personFields": "emailAddresses,metadata",
                        "pageSize": 1000,
                    }
                    if page_token:
                        kwargs["pageToken"] = page_token
                    resp = svc.people().connections().list(**kwargs).execute()
                    for p in resp.get("connections", []) or []:
                        addrs = p.get("emailAddresses") or []
                        if addrs:
                            existing_by_email[addrs[0].get("value", "").lower()] = p["resourceName"]
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break

            created_count = updated_count = skipped = 0
            results: list[dict] = []
            with src.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = _csv.DictReader(fh)
                rows = list(reader)

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "contacts_import_csv",
                    {"rows": len(rows), "would_update": sum(1 for r in rows if (r.get("email","").lower() in existing_by_email))},
                )

            for row in rows:
                email = (row.get("email") or "").strip()
                if not email:
                    skipped += 1
                    results.append({"row": row.get("first_name"), "status": "skipped_no_email"})
                    continue

                custom_from_csv = {
                    k[len("custom."):]: v
                    for k, v in row.items()
                    if k.startswith("custom.") and v and not crm_stats.is_managed_key(k[len("custom."):])
                }
                phones = [row["phone"]] if row.get("phone") else None
                emails = [email]

                try:
                    match = existing_by_email.get(email.lower())
                    if match:
                        person = svc.people().get(
                            resourceName=match, personFields="metadata,userDefined"
                        ).execute()
                        body = _build_person_body(
                            first_name=row.get("first_name") or None,
                            last_name=row.get("last_name") or None,
                            emails=emails,
                            phones=phones,
                            organization=row.get("organization") or None,
                            title=row.get("title") or None,
                        )
                        # Merge custom fields without dropping managed.
                        existing_user = person.get("userDefined", []) or []
                        merged = [u for u in existing_user if u.get("key") not in custom_from_csv]
                        for k, v in custom_from_csv.items():
                            merged.append({"key": k, "value": v})
                        body["userDefined"] = merged
                        body["etag"] = person["etag"]
                        mask = _update_person_mask(body)
                        svc.people().updateContact(
                            resourceName=match,
                            updatePersonFields=mask,
                            body=body,
                        ).execute()
                        try:
                            crm_stats.apply_stats_to_contact(match, email)
                        except Exception:
                            pass
                        updated_count += 1
                        results.append({"email": email, "status": "updated", "resource_name": match})
                    else:
                        body = _build_person_body(
                            first_name=row.get("first_name") or None,
                            last_name=row.get("last_name") or None,
                            emails=emails,
                            phones=phones,
                            organization=row.get("organization") or None,
                            title=row.get("title") or None,
                            notes=row.get("notes") or None,
                            custom_fields=custom_from_csv or None,
                        )
                        created = svc.people().createContact(body=body).execute()
                        try:
                            crm_stats.apply_stats_to_contact(created["resourceName"], email)
                        except Exception:
                            pass
                        created_count += 1
                        results.append(
                            {"email": email, "status": "created", "resource_name": created["resourceName"]}
                        )
                except Exception as inner:
                    results.append({"email": email, "status": "failed", "error": str(inner)})

            return json.dumps(
                {
                    "total": len(results),
                    "created": created_count,
                    "updated": updated_count,
                    "skipped_no_email": skipped,
                    "results": results[:50],  # cap detail to avoid huge output
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)
