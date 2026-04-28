"""Tool modules. Each module exposes a `register(mcp)` function."""

from . import calendar as _calendar
from . import chat as _chat
from . import contacts as _contacts
from . import docs as _docs
from . import drive as _drive
from . import enrichment as _enrichment
from . import gmail as _gmail
from . import maps as _maps
from . import project_invoices as _project_invoices
from . import receipts as _receipts
from . import sheets as _sheets
from . import system as _system
from . import tasks as _tasks
from . import workflows as _workflows


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
    _workflows.register(mcp)
    _enrichment.register(mcp)
    _receipts.register(mcp)
    _project_invoices.register(mcp)
    _system.register(mcp)
