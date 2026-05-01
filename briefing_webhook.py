# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Local HTTP webhook for daily-standup action buttons.

Listens on 127.0.0.1:7799. The standup email's action buttons are URLs
like http://127.0.0.1:7799/briefing/action?token=ABC123 — clicking
fires the corresponding MCP action and returns a confirmation page.

The server runs in a daemon thread inside the MCP process. It only
accepts connections from localhost (loopback) — tokens never leave
your machine, and the server only runs while Cowork's MCP is running.

Endpoints:
    GET /                                — index (status + pending count)
    GET /briefing/action?token=ABC       — execute one action token
    GET /briefing/pending                — list pending tokens (debug)

Failure modes are visible — any error renders a clear page.
"""

from __future__ import annotations

import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

import briefing_actions
from logging_util import log


# Configurable via env var. Default 7799 (chosen to avoid common ports).
def _port() -> int:
    try:
        return int(os.environ.get("STANDUP_WEBHOOK_PORT", "7799"))
    except ValueError:
        return 7799


_HOST = "127.0.0.1"
_SERVER: Optional[HTTPServer] = None
_THREAD: Optional[threading.Thread] = None


# --------------------------------------------------------------------------- #
# Confirmation page renderer
# --------------------------------------------------------------------------- #


_PAGE_BASE = """<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
          background: #f6f7f9; margin: 0; padding: 60px 20px; }}
  .card {{ max-width: 560px; margin: 0 auto; background: #fff;
           border-radius: 12px; padding: 40px;
           box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  h1 {{ margin: 0 0 8px; font-size: 24px; color: {color}; }}
  .muted {{ color: #6a7079; font-size: 14px; }}
  pre {{ background: #f0f2f5; border-radius: 6px; padding: 12px;
         font-size: 12px; overflow: auto; max-height: 240px; }}
  .footer {{ margin-top: 24px; font-size: 12px; color: #909499; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px;
            font-size: 11px; font-weight: 700; letter-spacing: 1px;
            text-transform: uppercase; background: {color}; color: #fff; }}
</style></head>
<body><div class="card">
  <div class="badge">{badge}</div>
  <h1 style="margin-top: 14px;">{headline}</h1>
  <p class="muted">{detail}</p>
  {extra}
  <div class="footer">CoAssisted Workspace · Executive Briefing webhook</div>
</div></body></html>
"""


def _render_page(*, title: str, badge: str, headline: str,
                color: str, detail: str, extra: str = "") -> bytes:
    page = _PAGE_BASE.format(
        title=html.escape(title),
        badge=html.escape(badge),
        headline=html.escape(headline),
        color=color,
        detail=html.escape(detail),
        extra=extra,
    )
    return page.encode("utf-8")


def _result_extra(result: dict) -> str:
    return ('<pre>' + html.escape(json.dumps(result, indent=2, default=str)) + '</pre>')


# --------------------------------------------------------------------------- #
# Request handler
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    # Quiet logger — route through our logging instead of stderr
    def log_message(self, fmt, *args):
        log.info("standup_webhook %s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index":
            return self._serve_index()
        if parsed.path == "/briefing/action":
            qs = parse_qs(parsed.query)
            token = (qs.get("token") or [""])[0]
            return self._serve_action(token)
        if parsed.path == "/briefing/pending":
            return self._serve_pending()
        return self._serve_404()

    def do_POST(self):
        """Form-encoded POST (used by the editable-draft form) — accept the
        edited body and forward it as a body_override on the action payload."""
        parsed = urlparse(self.path)
        if parsed.path != "/briefing/action":
            return self._serve_404()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        form = parse_qs(raw)
        token = (form.get("token") or [""])[0]
        body_override = (form.get("body_override") or [""])[0]
        return self._serve_action(token, body_override=body_override or None)

    # ----------------------------------------------------------------- #

    def _serve_index(self):
        briefing_actions.expire_old()
        pending = briefing_actions.list_pending()
        body = _render_page(
            title="Executive Briefing webhook",
            badge="ACTIVE",
            headline="Executive Briefing webhook is running.",
            color="#1a4f8c",
            detail=f"{len(pending)} action token(s) pending.",
            extra='<p class="muted">Each button in your morning briefing routes here. '
                   'Click any button to fire its action.</p>',
        )
        self._send_html(200, body)

    def _serve_action(self, token: str, body_override: Optional[str] = None):
        if not token:
            body = _render_page(
                title="Missing token",
                badge="ERROR", headline="Missing token.",
                color="#a23a3a",
                detail="No token in the URL. Check the email button.",
            )
            return self._send_html(400, body)

        # Defer the import so the webhook can boot before MCP services exist
        from briefing_dispatcher import dispatch
        result = dispatch(token, body_override=body_override)

        if "error" in (result.get("result") or {}) or "error" in result:
            err = (result.get("result") or {}).get("error") or result.get("error")
            body = _render_page(
                title="Action failed",
                badge="FAILED", headline="Action did not complete.",
                color="#a23a3a",
                detail=str(err),
                extra=_result_extra(result),
            )
            return self._send_html(500, body)

        if result.get("skipped"):
            body = _render_page(
                title="Already done",
                badge="ALREADY DONE",
                headline="This action was already executed.",
                color="#6a7079",
                detail=f"Status: {result.get('status')}",
                extra=_result_extra(result),
            )
            return self._send_html(200, body)

        kind = (result.get("kind") or "").replace("_", " ")
        body = _render_page(
            title="Action complete",
            badge="DONE",
            headline=f"{kind.title()} — done.",
            color="#2d6e3e",
            detail="You can close this tab and return to the briefing.",
            extra=_result_extra(result),
        )
        self._send_html(200, body)

    def _serve_pending(self):
        briefing_actions.expire_old()
        pending = briefing_actions.list_pending()
        rows = "".join(
            f"<tr><td><code>{html.escape(p['token'])}</code></td>"
            f"<td>{html.escape(p['kind'])}</td>"
            f"<td>{html.escape(p.get('label') or '')}</td></tr>"
            for p in pending
        )
        table = (
            "<table style='width:100%;border-collapse:collapse;'>"
            "<tr style='text-align:left;border-bottom:1px solid #ccc;'>"
            "<th>Token</th><th>Kind</th><th>Label</th></tr>"
            f"{rows or '<tr><td colspan=3 class=muted>none</td></tr>'}"
            "</table>"
        )
        body = _render_page(
            title="Pending standup actions",
            badge="PENDING", headline=f"{len(pending)} pending action(s).",
            color="#1a4f8c", detail="",
            extra=table,
        )
        self._send_html(200, body)

    def _serve_404(self):
        body = _render_page(
            title="Not found", badge="404",
            headline="Not found.", color="#a23a3a",
            detail="That route isn't handled here.",
        )
        self._send_html(404, body)

    def _send_html(self, status: int, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def start(*, host: str = _HOST, port: Optional[int] = None) -> dict:
    """Boot the webhook in a daemon thread. Idempotent — second call returns
    the existing server's address."""
    global _SERVER, _THREAD
    if _SERVER is not None:
        return {"already_running": True,
                "url": f"http://{_SERVER.server_address[0]}:{_SERVER.server_address[1]}"}
    p = port if port is not None else _port()
    try:
        _SERVER = HTTPServer((host, p), _Handler)
    except OSError as e:
        log.warning("standup_webhook: failed to bind %s:%d — %s", host, p, e)
        return {"started": False, "error": str(e)}
    _THREAD = threading.Thread(
        target=_SERVER.serve_forever, name="standup-webhook", daemon=True,
    )
    _THREAD.start()
    log.info("standup_webhook: listening on http://%s:%d", host, p)
    return {"started": True, "url": f"http://{host}:{p}"}


def stop() -> dict:
    global _SERVER, _THREAD
    if _SERVER is None:
        return {"running": False}
    _SERVER.shutdown()
    _SERVER.server_close()
    _SERVER = None
    _THREAD = None
    return {"stopped": True}


def is_running() -> bool:
    return _SERVER is not None


def url() -> str:
    """Return the base URL the email buttons should link to. If the server
    isn't running yet, returns the configured port (caller can still build
    URLs that will work once the server starts).
    """
    if _SERVER is not None:
        host, p = _SERVER.server_address
        return f"http://{host}:{p}"
    return f"http://{_HOST}:{_port()}"


def action_url(token: str) -> str:
    """The URL an action button should point to."""
    return f"{url()}/briefing/action?token={token}"
