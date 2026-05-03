"""Tool modules. Each module exposes a `register(mcp)` function."""

from . import access_audit as _access_audit
from . import ap_journal as _ap_journal
from . import ap_tree as _ap_tree
from . import ap_wave3 as _ap_wave3
from . import calendar as _calendar
from . import executive_briefing as _executive_briefing
from . import chat as _chat
from . import contacts as _contacts
from . import contract_bundle as _contract_bundle
from . import draft_queue as _draft_queue
from . import docs as _docs
from . import drive as _drive
from . import enrichment as _enrichment
from . import gmail as _gmail
from . import handoff as _handoff
from . import maps as _maps
from . import morning_brief as _morning_brief
from . import p2_workflows as _p2_workflows
from . import p3_workflows as _p3_workflows
from . import p4_workflows as _p4_workflows
from . import p5_workflows as _p5_workflows
from . import p6_workflows as _p6_workflows
from . import p7_workflows as _p7_workflows
from . import project_invoices as _project_invoices
from . import receipts as _receipts
from . import reply_all_guard as _reply_all_guard
from . import scanner as _scanner
from . import schedule_defrag as _schedule_defrag
from . import sheets as _sheets
from . import system as _system
from . import tasks as _tasks
from . import workflows_gmail as _workflows_gmail
from . import workflows_crm as _workflows_crm
from . import workflows_calendar as _workflows_calendar
from . import workflows_chat as _workflows_chat
from . import workflows_misc as _workflows_misc
# PandaDoc — Wave 4 e-signature backend (122 generated tools + 5 workflows).
from . import pandadoc_documents as _pandadoc_documents
from . import pandadoc_templates as _pandadoc_templates
from . import pandadoc_workspace as _pandadoc_workspace
from . import pandadoc_content as _pandadoc_content
from . import pandadoc_webhooks as _pandadoc_webhooks
from . import pandadoc_misc as _pandadoc_misc
from . import pandadoc_workflows as _pandadoc_workflows
# StaffWizard — daily-operations pipeline (6 tools).
from . import staffwizard as _staffwizard


def register_all(mcp) -> None:
    """Register every service's tools with the given FastMCP instance."""
    _gmail.register(mcp)
    _calendar.register(mcp)
    _drive.register(mcp)
    _sheets.register(mcp)
    _docs.register(mcp)
    _tasks.register(mcp)
    _contacts.register(mcp)
    _chat.register(mcp)
    _maps.register(mcp)
    _workflows_gmail.register(mcp)
    _workflows_crm.register(mcp)
    _workflows_calendar.register(mcp)
    _workflows_chat.register(mcp)
    _workflows_misc.register(mcp)
    _enrichment.register(mcp)
    _receipts.register(mcp)
    _project_invoices.register(mcp)
    _reply_all_guard.register(mcp)
    _access_audit.register(mcp)
    _morning_brief.register(mcp)
    _executive_briefing.register(mcp)
    _schedule_defrag.register(mcp)
    _contract_bundle.register(mcp)
    _draft_queue.register_all(mcp)
    _p2_workflows.register(mcp)
    _p3_workflows.register(mcp)
    _p4_workflows.register(mcp)
    _p5_workflows.register(mcp)
    _p6_workflows.register(mcp)
    _p7_workflows.register(mcp)
    _scanner.register(mcp)
    _system.register(mcp)
    _handoff.register(mcp)
    _ap_journal.register(mcp)
    _ap_tree.register(mcp)
    _ap_wave3.register(mcp)
    # PandaDoc — Wave 4 e-signature backend.
    _pandadoc_documents.register(mcp)
    _pandadoc_templates.register(mcp)
    _pandadoc_workspace.register(mcp)
    _pandadoc_content.register(mcp)
    _pandadoc_webhooks.register(mcp)
    _pandadoc_misc.register(mcp)
    _pandadoc_workflows.register(mcp)
    # StaffWizard — daily-operations pipeline.
    _staffwizard.register(mcp)
