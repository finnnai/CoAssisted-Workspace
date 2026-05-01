# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Cross-service workflow tools — split into category modules during P1-1.

Backwards-compat re-export shim. New code should import directly from
tools.workflows_{gmail,crm,calendar,chat,misc}. This module exists so that
tests + external callers that did `from tools.workflows import _haversine_km`
or `tools.workflows.ReceiptChatDigestInput` keep working without edits.

Note: this module no longer has a `register()` — registration happens via
tools/__init__.py calling each of the 5 split modules directly. If you rely
on `tools.workflows.register(mcp)`, that's been moved to
tools.workflows_gmail.register(mcp) + the other 4 module registers.
"""
from __future__ import annotations

# Module-level service handle re-export so test patches like
# `patch.object(wf.gservices, "maps", ...)` still resolve.
import gservices  # noqa: F401

# Helpers — every underscore-prefixed function the legacy module exposed.
from tools._workflow_helpers import (  # noqa: F401
    _build_simple_email,
    _calendar_svc,
    _contact_lat_lng,
    _drive,
    _extract_address_block,
    _extract_display_name,
    _extract_plaintext,
    _geocode_cache_path,
    _geocode_cached,
    _gmail,
    _guess_name_from_email,
    _haversine_km,
    _list_attachments_meta,
    _load_geocode_cache,
    _log_activity_on_contact,
    _parse_email_address,
    _resolve_current_location,
    _resolve_recipient,
    _resolve_to_address,
    _save_geocode_cache,
    _set_contact_custom_fields,
    _split_display_name,
    _strip_reply_prefixes,
    _walk_all_contacts,
)

# Module globals (geocode cache state).
from tools._workflow_helpers import (  # noqa: F401
    _GEOCODE_CACHE,
    _GEOCODE_CACHE_PATH,
)

# Input model classes — re-exported for legacy `wf.ClassName` access.
from tools.workflows_gmail import (  # noqa: F401
    EmailDocAsPdfInput,
    EmailThreadToEventInput,
    EmailWithMapInput,
    GetTemplateInput,
    ListTemplatesInput,
    RecipientInput,
    SaveAttachmentsToDriveInput,
    SendHandoffArchiveInput,
    SendMailMergeByNameInput,
    SendMailMergeInput,
    SendTemplatedByNameInput,
    SendTemplatedInput,
    ShareDriveFileViaEmailInput,
)
from tools.workflows_crm import (  # noqa: F401
    AddressHygieneAuditInput,
    BulkContactPatch,
    BulkUpdateContactsInput,
    ContactDensityMapInput,
    CreateContactsFromSentMailInput,
    GeocodeContactsBatchInput,
    NearbyContactsInput,
    SeedResponseTemplatesInput,
    SmartFollowupFinderInput,
    SuggestResponseTemplateInput,
)
from tools.workflows_calendar import (  # noqa: F401
    AdvRouteOptimizeInput,
    AdvRouteStop,
    AdvRouteVehicle,
    CalendarDriveTimeBlocksInput,
    CommuteBriefInput,
    DepartureReminderInput,
    DetectOooInput,
    ErrandRouteInput,
    EventNearbyAmenitiesInput,
    FindMeetingSlotInput,
    MeetingLocationOptionsInput,
    MeetingMidpointInput,
    RecentMeetingsHeatmapInput,
    RemoveDriveTimeBlocksInput,
    RouteFromCalendarInput,
    RouteOptimizeVisitsInput,
    TravelBriefInput,
)
from tools.workflows_chat import (  # noqa: F401
    ChatDigestWorkflowInput,
    ChatMeetingBriefAttendee,
    ChatMeetingBriefInput,
    ChatSharePlaceInput,
    ChatToContactGroupWorkflowInput,
    ChatWithMapInput,
)
from tools.workflows_misc import (  # noqa: F401
    MonthlyExpenseReportInput,
    ReceiptChatDigestInput,
)


# --------------------------------------------------------------------------- #
# Backwards-compat register() — delegates to all 5 split modules.
# tools/__init__.py does NOT call this (it imports each module directly), so
# this only matters for callers that historically did `tools.workflows.register(mcp)`.
# Tests that scan all tools/*.py and call register on each must explicitly
# skip the workflows shim to avoid double-registration.
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    """Register all tools from the 5 category modules. Back-compat only —
    tools/__init__.py invokes the 5 modules directly. If you call this from
    your own setup code AND let __init__.py run, you'll double-register."""
    from tools.workflows_gmail import register as _g
    from tools.workflows_crm import register as _c
    from tools.workflows_calendar import register as _cal
    from tools.workflows_chat import register as _ch
    from tools.workflows_misc import register as _m
    _g(mcp); _c(mcp); _cal(mcp); _ch(mcp); _m(mcp)
