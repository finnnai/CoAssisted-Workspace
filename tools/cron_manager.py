# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Cron management MCP tools (v0.9.3).

Tools registered:

    workflow_cron_list                — show current schedule
    workflow_cron_toggle              — enable/disable a job
    workflow_cron_update_schedule     — change a job's cron expression
    workflow_cron_update_command      — change a job's command
    workflow_cron_add_job             — add a new managed job
    workflow_cron_remove_job          — remove a managed job
    workflow_cron_install             — apply changes to the live crontab
    workflow_cron_render_static_page  — produce read-only HTML view
    workflow_cron_publish_to_drive    — upload static page to Drive,
                                        return shareable URL
    workflow_cron_open_artifact       — return the Cowork artifact URL
                                        (or render the page text inline
                                        when Cowork isn't available)
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import cron_manager as _cm


_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pydantic input models
# --------------------------------------------------------------------------- #


class _ListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_only: bool = Field(False, description="If True, only show enabled jobs.")


class _ToggleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    job_id: str = Field(..., description="Job id from workflow_cron_list.")
    enabled: Optional[bool] = Field(
        None, description="True/False to set explicitly. Omit to flip current state.",
    )
    install_now: bool = Field(
        True, description="Apply the change to the live crontab immediately.",
    )


class _UpdateScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    job_id: str = Field(..., description="Job id.")
    schedule: str = Field(
        ..., description="5-field cron expression (e.g. '30 6 * * *' for 6:30 daily).",
    )
    install_now: bool = Field(True)


class _UpdateCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    job_id: str = Field(..., description="Job id.")
    command: str = Field(
        ...,
        description=(
            "Shell command. Use $HOME and $VENV_PYTHON placeholders; "
            "the installer substitutes them at install time."
        ),
    )
    install_now: bool = Field(True)


class _AddJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(  # noqa: A003
        ..., min_length=2, max_length=40,
        description="Short slug (lowercase, dashes/underscores). Must be unique.",
    )
    name: str = Field(..., min_length=2, max_length=120, description="Human label.")
    schedule: str = Field(..., description="5-field cron expression.")
    command: str = Field(..., description="Shell command. $HOME / $VENV_PYTHON OK.")
    description: str = Field("", description="One-line explanation.")
    category: Optional[str] = Field(
        "Custom",
        description=(
            "Optional grouping for the management UI. Common values: "
            "AP/AR, AP/AR — labor, CRM, Vendors, Briefings, Other, Custom."
        ),
    )
    enabled: bool = Field(True)
    install_now: bool = Field(True)


class _RemoveJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    job_id: str = Field(..., description="Job id.")
    install_now: bool = Field(True)


class _InstallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RenderStaticPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    out_path: Optional[str] = Field(
        None,
        description=(
            "Optional output path. Default: ~/cron_schedule.html — same dir "
            "the executive briefing footer links to."
        ),
    )


class _PublishToDriveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    drive_folder_id: Optional[str] = Field(
        None,
        description=(
            "Drive folder to upload to. If omitted, uses "
            "config.cron_manager.drive_folder_id (or root)."
        ),
    )
    file_name: str = Field(
        "Surefox Cron Schedule.html",
        description="Display name for the uploaded file.",
    )


class _OpenArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _err(e: Exception) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _maybe_install(install_now: bool) -> Optional[dict]:
    if not install_now:
        return None
    try:
        return _cm.install_crontab()
    except Exception as e:
        return {"status": "install_error", "error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------- #
# Static page generator
# --------------------------------------------------------------------------- #


_STATIC_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Surefox Cron Schedule</title>
<style>
  :root {{
    --bg: #0c0d10;
    --panel: #161821;
    --panel-2: #1f2230;
    --fg: #e9ecf3;
    --muted: #8b91a6;
    --green: #54c08d;
    --red: #e16464;
    --amber: #d9a248;
    --link: #6ea8ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    font: 14px/1.45 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--fg);
  }}
  .wrap {{ max-width: 920px; margin: 0 auto; }}
  h1 {{ margin: 0 0 4px 0; font-size: 22px; }}
  .sub {{ color: var(--muted); margin-bottom: 24px; font-size: 13px; }}
  .stats {{
    display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px;
  }}
  .stat {{
    background: var(--panel); padding: 12px 16px; border-radius: 8px;
    flex: 1; min-width: 140px;
  }}
  .stat .num {{ font-size: 22px; font-weight: 600; }}
  .stat .lbl {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
  .cat-block {{ background: var(--panel); border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .cat-name {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }}
  .row {{
    display: grid; grid-template-columns: auto 1fr auto; gap: 12px;
    padding: 10px 12px; border-radius: 6px; align-items: center;
  }}
  .row + .row {{ margin-top: 4px; }}
  .row.disabled {{ opacity: 0.45; }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 600;
  }}
  .badge.on {{ background: rgba(84, 192, 141, 0.18); color: var(--green); }}
  .badge.off {{ background: rgba(225, 100, 100, 0.18); color: var(--red); }}
  .name {{ font-weight: 600; }}
  .desc {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
  .schedule {{ font-family: ui-monospace, SFMono-Regular, monospace; color: var(--amber); font-size: 13px; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--panel-2); color: var(--muted); font-size: 12px; }}
  .footer a {{ color: var(--link); text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
  .cta {{
    display: inline-block; margin-top: 12px; padding: 8px 14px;
    background: var(--link); color: white; border-radius: 6px;
    text-decoration: none; font-weight: 600;
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Surefox Cron Schedule</h1>
  <div class="sub">Read-only snapshot · generated {generated_at}</div>

  <div class="stats">
    <div class="stat"><div class="num">{enabled}</div><div class="lbl">Enabled</div></div>
    <div class="stat"><div class="num">{disabled}</div><div class="lbl">Disabled</div></div>
    <div class="stat"><div class="num">{total}</div><div class="lbl">Total jobs</div></div>
  </div>

  {body}

  <div class="footer">
    To toggle, edit, or add jobs, ask Claude in Cowork:<br />
    <code>"open the cron manager"</code>
    <br /><br />
    Source of truth: <code>cron_jobs.json</code> · live crontab is rewritten by
    <code>workflow_cron_install</code> on every change.
  </div>
</div>
</body>
</html>
"""


def _render_static_page(jobs: list[dict], stats: dict) -> str:
    by_cat: dict[str, list[dict]] = {}
    for j in jobs:
        cat = j.get("category") or "Other"
        by_cat.setdefault(cat, []).append(j)

    body_chunks: list[str] = []
    for cat in sorted(by_cat.keys()):
        body_chunks.append(f'<div class="cat-block"><div class="cat-name">{_html_escape(cat)}</div>')
        for j in by_cat[cat]:
            cls = "row" + ("" if j.get("enabled", True) else " disabled")
            badge_cls = "on" if j.get("enabled", True) else "off"
            badge_text = "ON" if j.get("enabled", True) else "OFF"
            body_chunks.append(
                f'<div class="{cls}">'
                f'<span class="badge {badge_cls}">{badge_text}</span>'
                f'<div><div class="name">{_html_escape(j.get("name") or j.get("id"))}</div>'
                f'<div class="desc">{_html_escape(j.get("description") or "")}</div></div>'
                f'<div class="schedule">{_html_escape(j.get("schedule") or "")}</div>'
                f'</div>'
            )
        body_chunks.append("</div>")

    return _STATIC_PAGE_TEMPLATE.format(
        generated_at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        enabled=stats.get("enabled_jobs", 0),
        disabled=stats.get("disabled_jobs", 0),
        total=stats.get("total_jobs", 0),
        body="\n".join(body_chunks),
    )


def _html_escape(s: str) -> str:
    if not s:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# --------------------------------------------------------------------------- #
# Cowork artifact HTML — interactive editor
# --------------------------------------------------------------------------- #


COWORK_ARTIFACT_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Surefox Cron Manager</title>
<style>
  :root {
    --bg: #0c0d10; --panel: #161821; --panel-2: #1f2230;
    --fg: #e9ecf3; --muted: #8b91a6;
    --green: #54c08d; --red: #e16464; --amber: #d9a248; --link: #6ea8ff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px;
    font: 14px/1.45 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--fg);
  }
  h1 { margin: 0 0 4px 0; font-size: 18px; }
  .sub { color: var(--muted); font-size: 12px; margin-bottom: 14px; }
  button {
    background: var(--panel-2); color: var(--fg); border: 1px solid var(--panel-2);
    padding: 6px 12px; border-radius: 6px; cursor: pointer; font: inherit;
  }
  button:hover { border-color: var(--link); }
  button.primary { background: var(--link); color: white; border-color: var(--link); }
  button.danger { background: rgba(225, 100, 100, 0.15); color: var(--red); border-color: rgba(225, 100, 100, 0.3); }
  .toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .stat-row { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
  .stat-row .stat { background: var(--panel); padding: 6px 12px; border-radius: 6px; font-size: 12px; }
  .cat { background: var(--panel); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
  .cat-name { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
  .row { display: grid; grid-template-columns: auto 1fr auto auto; gap: 10px; align-items: center; padding: 8px; border-radius: 6px; }
  .row + .row { margin-top: 3px; }
  .row.disabled { opacity: 0.5; }
  .name { font-weight: 600; }
  .desc { color: var(--muted); font-size: 11px; margin-top: 1px; }
  .schedule { font-family: ui-monospace, SFMono-Regular, monospace; color: var(--amber); font-size: 12px; }
  .schedule input { font-family: inherit; background: var(--panel-2); color: var(--amber); border: 1px solid var(--panel-2); border-radius: 4px; padding: 2px 6px; width: 130px; }
  .toggle { width: 44px; height: 24px; border-radius: 12px; cursor: pointer; position: relative; transition: background 0.15s; flex-shrink: 0; }
  .toggle.on { background: var(--green); }
  .toggle.off { background: rgba(225, 100, 100, 0.6); }
  .toggle::after { content: ""; position: absolute; top: 2px; width: 20px; height: 20px; background: white; border-radius: 50%; transition: left 0.15s; }
  .toggle.on::after { left: 22px; }
  .toggle.off::after { left: 2px; }
  .add-form { background: var(--panel); padding: 16px; border-radius: 8px; margin-top: 16px; display: none; }
  .add-form.open { display: block; }
  .add-form input, .add-form textarea, .add-form select { width: 100%; background: var(--panel-2); color: var(--fg); border: 1px solid var(--panel-2); border-radius: 4px; padding: 6px 8px; font: inherit; margin-top: 6px; }
  .add-form label { display: block; margin-bottom: 8px; font-size: 12px; color: var(--muted); }
  .err { color: var(--red); font-size: 12px; margin-top: 4px; }
  .ok { color: var(--green); font-size: 12px; margin-top: 4px; }
  .help { color: var(--muted); font-size: 11px; margin-top: 4px; }
</style>
</head>
<body>
  <h1>Surefox Cron Manager</h1>
  <div class="sub" id="sub">Loading…</div>

  <div class="toolbar">
    <button class="primary" id="add-btn">+ Add job</button>
    <button id="install-btn">Apply to crontab</button>
    <button id="reload-btn">Refresh</button>
  </div>

  <div class="stat-row" id="stats"></div>
  <div id="status"></div>
  <div id="categories"></div>

  <div class="add-form" id="add-form">
    <h3 style="margin: 0 0 12px 0; font-size: 14px;">Add a new job</h3>
    <label>Slug (id): <input id="add-id" placeholder="e.g. backup_drive" /></label>
    <label>Name: <input id="add-name" placeholder="Backup Drive folder" /></label>
    <label>Schedule (5-field cron):
      <input id="add-schedule" placeholder="0 3 * * *" />
      <div class="help">min hour day-of-month month day-of-week. Examples: <code>0 3 * * *</code> = 3am daily, <code>*/15 8-18 * * 1-5</code> = every 15 min biz hours.</div>
    </label>
    <label>Command:
      <input id="add-command" placeholder="$VENV_PYTHON $HOME/my_script.py >> $HOME/logs/my_script.log 2>&amp;1" />
      <div class="help">Use <code>$HOME</code> and <code>$VENV_PYTHON</code>; substituted at install time.</div>
    </label>
    <label>Description: <input id="add-description" placeholder="What this job does" /></label>
    <label>Category:
      <select id="add-category">
        <option>Custom</option>
        <option>AP/AR</option>
        <option>AP/AR — labor</option>
        <option>CRM</option>
        <option>Vendors</option>
        <option>Briefings</option>
        <option>Other</option>
      </select>
    </label>
    <div style="display: flex; gap: 8px; margin-top: 12px;">
      <button class="primary" id="add-submit">Add + apply</button>
      <button id="add-cancel">Cancel</button>
    </div>
    <div id="add-err" class="err"></div>
  </div>

<script>
const TOOLS = {
  list: 'workflow_cron_list',
  toggle: 'workflow_cron_toggle',
  updateSchedule: 'workflow_cron_update_schedule',
  add: 'workflow_cron_add_job',
  remove: 'workflow_cron_remove_job',
  install: 'workflow_cron_install',
};

function setStatus(msg, kind='ok') {
  const el = document.getElementById('status');
  el.innerHTML = `<div class="${kind}">${msg}</div>`;
  setTimeout(() => { if (el.innerHTML.includes(msg)) el.innerHTML = ''; }, 4000);
}

async function call(toolName, args) {
  if (typeof window.cowork === 'undefined' || !window.cowork.callMcpTool) {
    throw new Error('Cowork bridge not available — open this artifact inside Cowork.');
  }
  return await window.cowork.callMcpTool(toolName, args || {});
}

async function refresh() {
  try {
    const res = await call(TOOLS.list, { enabled_only: false });
    render(res);
  } catch (e) {
    document.getElementById('sub').textContent = 'Error: ' + e.message;
  }
}

function render(payload) {
  const jobs = payload.jobs || [];
  const stats = payload.stats || {};
  document.getElementById('sub').textContent =
    `${stats.enabled_jobs || 0} enabled / ${stats.total_jobs || jobs.length} total`;

  const statsEl = document.getElementById('stats');
  statsEl.innerHTML = '';
  for (const [k, v] of Object.entries(stats.categories || {})) {
    statsEl.insertAdjacentHTML('beforeend', `<div class="stat">${k}: <strong>${v}</strong></div>`);
  }

  const cats = {};
  for (const j of jobs) {
    const c = j.category || 'Other';
    (cats[c] = cats[c] || []).push(j);
  }

  const wrap = document.getElementById('categories');
  wrap.innerHTML = '';
  for (const cat of Object.keys(cats).sort()) {
    let html = `<div class="cat"><div class="cat-name">${escapeHtml(cat)}</div>`;
    for (const j of cats[cat]) {
      const enabled = j.enabled !== false;
      html += `
        <div class="row ${enabled ? '' : 'disabled'}" data-id="${escapeHtml(j.id)}">
          <div class="toggle ${enabled ? 'on' : 'off'}" data-action="toggle" title="${enabled ? 'Click to disable' : 'Click to enable'}"></div>
          <div>
            <div class="name">${escapeHtml(j.name || j.id)}</div>
            <div class="desc">${escapeHtml(j.description || '')}</div>
          </div>
          <div class="schedule"><input value="${escapeHtml(j.schedule || '')}" data-action="schedule" /></div>
          <button class="danger" data-action="remove" title="Remove">×</button>
        </div>`;
    }
    html += `</div>`;
    wrap.insertAdjacentHTML('beforeend', html);
  }

  // Wire up actions.
  wrap.querySelectorAll('.toggle').forEach(el => {
    el.addEventListener('click', async (e) => {
      const id = e.target.closest('.row').dataset.id;
      try {
        await call(TOOLS.toggle, { job_id: id, install_now: true });
        setStatus(`Toggled ${id}.`);
        refresh();
      } catch (e) { setStatus('Error: ' + e.message, 'err'); }
    });
  });
  wrap.querySelectorAll('input[data-action="schedule"]').forEach(el => {
    el.addEventListener('change', async (e) => {
      const id = e.target.closest('.row').dataset.id;
      try {
        const res = await call(TOOLS.updateSchedule, { job_id: id, schedule: e.target.value, install_now: true });
        if (res && res.status === 'invalid_schedule') {
          setStatus('Invalid schedule: ' + res.reason, 'err');
        } else {
          setStatus(`Updated ${id} schedule.`);
        }
      } catch (err) { setStatus('Error: ' + err.message, 'err'); }
    });
  });
  wrap.querySelectorAll('button[data-action="remove"]').forEach(el => {
    el.addEventListener('click', async (e) => {
      const id = e.target.closest('.row').dataset.id;
      if (!confirm(`Remove ${id}? This cannot be undone (the job stops running).`)) return;
      try {
        await call(TOOLS.remove, { job_id: id, install_now: true });
        setStatus(`Removed ${id}.`);
        refresh();
      } catch (e) { setStatus('Error: ' + e.message, 'err'); }
    });
  });
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}

document.getElementById('reload-btn').addEventListener('click', refresh);
document.getElementById('install-btn').addEventListener('click', async () => {
  try {
    const r = await call(TOOLS.install, {});
    setStatus(`Installed: ${r.enabled_jobs || 0} enabled / ${r.total_jobs || 0} total.`);
  } catch (e) { setStatus('Error: ' + e.message, 'err'); }
});
document.getElementById('add-btn').addEventListener('click', () => {
  document.getElementById('add-form').classList.toggle('open');
});
document.getElementById('add-cancel').addEventListener('click', () => {
  document.getElementById('add-form').classList.remove('open');
});
document.getElementById('add-submit').addEventListener('click', async () => {
  const args = {
    id: document.getElementById('add-id').value.trim(),
    name: document.getElementById('add-name').value.trim(),
    schedule: document.getElementById('add-schedule').value.trim(),
    command: document.getElementById('add-command').value.trim(),
    description: document.getElementById('add-description').value.trim(),
    category: document.getElementById('add-category').value,
    enabled: true,
    install_now: true,
  };
  const errEl = document.getElementById('add-err');
  errEl.textContent = '';
  try {
    const res = await call(TOOLS.add, args);
    if (res && res.status && res.status !== 'ok') {
      errEl.textContent = res.status + ': ' + (res.reason || res.error || '');
      return;
    }
    setStatus(`Added ${args.id}.`);
    document.getElementById('add-form').classList.remove('open');
    ['add-id','add-name','add-schedule','add-command','add-description'].forEach(i => document.getElementById(i).value = '');
    refresh();
  } catch (e) { errEl.textContent = e.message; }
});

refresh();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# MCP registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:  # noqa: ANN001
    """Register all cron-management tools."""

    @mcp.tool()
    def workflow_cron_list(params: _ListInput) -> dict[str, Any]:
        """List managed cron jobs with their schedule, command, and
        enabled/disabled state. Used by the management UI to render the
        current state.
        """
        try:
            jobs = _cm.list_jobs(enabled_only=params.enabled_only)
            return {
                "status": "ok",
                "jobs": jobs,
                "stats": _cm.stats(),
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_toggle(params: _ToggleInput) -> dict[str, Any]:
        """Enable or disable a job. Pass enabled=true/false to set
        explicitly, or omit to flip current state. install_now=True
        applies the change to the live crontab immediately.
        """
        try:
            res = _cm.toggle(params.job_id, enabled=params.enabled)
            if res.get("status") == "ok" and params.install_now:
                res["installed"] = _maybe_install(True)
            return res
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_update_schedule(params: _UpdateScheduleInput) -> dict[str, Any]:
        """Change a job's cron expression. Validates 5-field format and
        (if available) runs croniter for deeper validation. install_now
        applies to the live crontab.
        """
        try:
            res = _cm.update_schedule(params.job_id, params.schedule)
            if res.get("status") == "ok" and params.install_now:
                res["installed"] = _maybe_install(True)
            return res
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_update_command(params: _UpdateCommandInput) -> dict[str, Any]:
        """Change a job's shell command. Use $HOME / $VENV_PYTHON
        placeholders; substituted at install time.
        """
        try:
            res = _cm.update_command(params.job_id, params.command)
            if res.get("status") == "ok" and params.install_now:
                res["installed"] = _maybe_install(True)
            return res
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_add_job(params: _AddJobInput) -> dict[str, Any]:
        """Add a new managed cron job. id must be unique. The new job is
        appended to cron_jobs.json; install_now applies it to the live
        crontab.
        """
        try:
            res = _cm.add_job(
                id=params.id, name=params.name, schedule=params.schedule,
                command=params.command, description=params.description,
                category=params.category, enabled=params.enabled,
            )
            if res.get("status") == "ok" and params.install_now:
                res["installed"] = _maybe_install(True)
            return res
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_remove_job(params: _RemoveJobInput) -> dict[str, Any]:
        """Remove a managed cron job. The job stops running on the next
        install (which fires immediately if install_now=True).
        """
        try:
            removed = _cm.remove_job(params.job_id)
            out: dict[str, Any] = {
                "status": "ok" if removed else "not_found",
                "removed": removed, "job_id": params.job_id,
            }
            if removed and params.install_now:
                out["installed"] = _maybe_install(True)
            return out
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_install(params: _InstallInput) -> dict[str, Any]:
        """Apply the current cron_jobs.json state to the live crontab.
        Preserves the operator's personal cron entries (anything outside
        the CoAssisted-managed marker block).
        """
        try:
            return _cm.install_crontab()
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_render_static_page(
        params: _RenderStaticPageInput,
    ) -> dict[str, Any]:
        """Generate a self-contained read-only HTML page showing the
        current schedule. Saved to disk so the executive briefing footer
        can link to it (or the operator can email/share).
        """
        try:
            jobs = _cm.list_jobs(enabled_only=False)
            stats = _cm.stats()
            html = _render_static_page(jobs, stats)
            out_path = Path(params.out_path).expanduser() if params.out_path \
                else (Path.home() / "cron_schedule.html")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            return {
                "status": "ok",
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size,
                "enabled_jobs": stats.get("enabled_jobs", 0),
                "total_jobs": stats.get("total_jobs", 0),
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_publish_to_drive(
        params: _PublishToDriveInput,
    ) -> dict[str, Any]:
        """Render the static schedule page and upload it to Drive,
        returning the shareable URL the executive briefing footer links to.
        """
        try:
            jobs = _cm.list_jobs(enabled_only=False)
            stats = _cm.stats()
            html = _render_static_page(jobs, stats)
            return _upload_html_to_drive(
                html, file_name=params.file_name,
                drive_folder_id=params.drive_folder_id,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_cron_open_artifact(params: _OpenArtifactInput) -> dict[str, Any]:
        """Return the interactive Cowork artifact HTML so Cowork can
        open it in a viewer pane. Inside Cowork the operator can toggle,
        edit schedules, add new jobs — every action calls back through
        the MCP tools above.

        If you're calling this outside Cowork, the returned `html` field
        still works as a standalone static view; it just won't be
        interactive without the Cowork bridge.
        """
        try:
            return {
                "status": "ok",
                "title": "Surefox Cron Manager",
                "mime_type": "text/html",
                "html": COWORK_ARTIFACT_HTML,
                "interactive": True,
                "calls": [
                    "workflow_cron_list", "workflow_cron_toggle",
                    "workflow_cron_update_schedule", "workflow_cron_add_job",
                    "workflow_cron_remove_job", "workflow_cron_install",
                ],
            }
        except Exception as e:
            return _err(e)


# --------------------------------------------------------------------------- #
# Drive upload glue (lazy — only imported when the tool runs)
# --------------------------------------------------------------------------- #


def _upload_html_to_drive(
    html: str, *, file_name: str, drive_folder_id: Optional[str] = None,
) -> dict[str, Any]:
    """Upload an HTML blob to Drive. Returns
    {status, file_id, file_url, web_view_link}.
    """
    import io

    import gservices  # type: ignore
    try:
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore
    except ImportError:
        return {"status": "error", "error": "googleapiclient.http not available"}

    drive = gservices.drive_service()

    folder_id = drive_folder_id
    if not folder_id:
        try:
            import config  # type: ignore
            block = config.get("cron_manager", {}) or {}
            folder_id = block.get("drive_folder_id")
        except Exception:
            folder_id = None

    media = MediaIoBaseUpload(
        io.BytesIO(html.encode("utf-8")),
        mimetype="text/html", resumable=False,
    )

    # See if a file by this name already exists in the target folder so
    # we update in place (stable URL across briefings).
    query_parts = [f"name = '{file_name.replace(chr(39), chr(39) + chr(39))}'", "trashed = false"]
    if folder_id:
        query_parts.append(f"'{folder_id}' in parents")
    query = " and ".join(query_parts)

    existing = drive.files().list(
        q=query, fields="files(id, webViewLink)", pageSize=1,
    ).execute()
    files = existing.get("files") or []

    if files:
        file_id = files[0]["id"]
        updated = drive.files().update(
            fileId=file_id, media_body=media,
            fields="id, webViewLink",
        ).execute()
        return {
            "status": "ok",
            "file_id": updated.get("id", file_id),
            "web_view_link": updated.get("webViewLink"),
            "updated_existing": True,
            "file_name": file_name,
        }

    metadata: dict = {"name": file_name, "mimeType": "text/html"}
    if folder_id:
        metadata["parents"] = [folder_id]
    created = drive.files().create(
        body=metadata, media_body=media,
        fields="id, webViewLink",
    ).execute()
    return {
        "status": "ok",
        "file_id": created.get("id"),
        "web_view_link": created.get("webViewLink"),
        "updated_existing": False,
        "file_name": file_name,
    }
