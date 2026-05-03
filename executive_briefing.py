# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Daily standup composer — pure-logic core.

Renders the daily briefing as a structured JSON spec + an HTML email body.
The MCP wrapper at tools/executive_briefing.py is responsible for fetching the
source data + actually sending the email.

Briefing has four sections:

    1. Header strip    — date + greeting
    2. Weather         — hourly forecast left-to-right with icons,
                          significant changes highlighted
    3. Email triage    — pre-drafted replies, per-thread action buttons:
                          [Approve send] [Schedule] [Mark read] [Mark as task]
    4. Day's meetings  — per-meeting action buttons:
                          [Accept] [Decline] [Suggest new time]
    5. Active tasks    — per-task action buttons:
                          [Complete] [Ignore] [Schedule]

Each actionable button is backed by a token registered with
briefing_actions.py. The button URL takes one of two forms:

    a) Direct deeplink to Gmail/Calendar/Tasks UI (works immediately)
    b) Cowork action URL (for buttons that require MCP-side execution)

Both forms are rendered into the email; tokens are also surfaced in
the JSON spec so power-users can run `workflow_briefing_execute_action`.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from dataclasses import dataclass, field
from typing import Iterable, Optional

import briefing_actions
import briefing_webhook
import news_feed
import weather as _weather
import weather_icons


# --------------------------------------------------------------------------- #
# Color palette (used for inline styles — no external CSS in email)
# --------------------------------------------------------------------------- #


COLORS = {
    "bg":        "#f6f7f9",
    "card_bg":   "#ffffff",
    "border":    "#e3e5e8",
    "text":      "#181a1f",
    "muted":     "#6a7079",
    "accent":    "#1a4f8c",      # navy — military feel
    "accent_lt": "#dce6f5",
    "weather_bar_bg": "#0d1f3a", # dark navy strip
    "weather_text":  "#ffffff",
    "highlight": "#fbe3a3",
    "btn_primary_bg":   "#1a4f8c",
    "btn_primary_text": "#ffffff",
    "btn_safe_bg":      "#2d6e3e",
    "btn_safe_text":    "#ffffff",
    "btn_danger_bg":    "#a23a3a",
    "btn_danger_text":  "#ffffff",
    "btn_neutral_bg":   "#eef0f3",
    "btn_neutral_text": "#181a1f",
}


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class ActionButton:
    """One action button rendered in the email."""
    label: str
    kind: str                # button kind (e.g. "approve_send")
    style: str               # "primary" | "safe" | "danger" | "neutral"
    href: str                # the URL the button points to
    token: Optional[str] = None  # action token for MCP execution

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "style": self.style,
            "href": self.href,
            "token": self.token,
        }


@dataclass
class EmailAttachment:
    """One attachment on an inbound email."""
    name: str
    url: str                  # link that opens the attachment (Gmail viewer or direct)
    mime_type: str = ""
    size_bytes: Optional[int] = None

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class EmailItem:
    """One inbound email needing triage."""
    thread_id: str
    sender_name: str
    sender_email: str
    subject: str
    snippet: str
    drafted_reply: str       # pre-composed brand-voice reply
    draft_id: Optional[str] = None  # Gmail draft ID once staged
    received_at: Optional[str] = None  # ISO timestamp for "2 hrs ago" footer
    attachments: list[EmailAttachment] = field(default_factory=list)
    actions: list[ActionButton] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "sender_name": self.sender_name,
            "sender_email": self.sender_email,
            "subject": self.subject,
            "snippet": self.snippet,
            "drafted_reply": self.drafted_reply,
            "draft_id": self.draft_id,
            "received_at": self.received_at,
            "attachments": [a.to_dict() for a in self.attachments],
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class MeetingItem:
    """One calendar event for today."""
    event_id: str
    summary: str
    start_iso: str
    end_iso: str
    start_label: str         # "9:00 AM"
    location: str = ""
    attendee_count: int = 0
    is_organizer: bool = False
    actions: list[ActionButton] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "summary": self.summary,
            "start_iso": self.start_iso,
            "end_iso": self.end_iso,
            "start_label": self.start_label,
            "location": self.location,
            "attendee_count": self.attendee_count,
            "is_organizer": self.is_organizer,
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class TaskItem:
    """One Google Task on the active list."""
    task_id: str
    tasklist_id: str
    title: str
    notes: str
    due_iso: Optional[str]
    actions: list[ActionButton] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "tasklist_id": self.tasklist_id,
            "title": self.title,
            "notes": self.notes,
            "due_iso": self.due_iso,
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class ExecutiveBriefing:
    """Full briefing — JSON-serializable + renderable as HTML email."""
    date: str
    greeting_name: str
    user_email: str
    weather: Optional[_weather.DailyForecast]
    weather_significant_change_idx: list[int] = field(default_factory=list)
    emails: list[EmailItem] = field(default_factory=list)
    meetings: list[MeetingItem] = field(default_factory=list)
    tasks: list[TaskItem] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "greeting_name": self.greeting_name,
            "user_email": self.user_email,
            "weather": self.weather.to_dict() if self.weather else None,
            "weather_significant_change_idx": list(self.weather_significant_change_idx),
            "emails": [e.to_dict() for e in self.emails],
            "meetings": [m.to_dict() for m in self.meetings],
            "tasks": [t.to_dict() for t in self.tasks],
            "news": list(self.news),
            "summary": self.summary_line(),
        }

    def summary_line(self) -> str:
        return (f"{len(self.emails)} emails · "
                f"{len(self.meetings)} meetings · "
                f"{len(self.tasks)} active tasks")


# --------------------------------------------------------------------------- #
# Action token + URL helpers
# --------------------------------------------------------------------------- #


# Action URL — points at the local webhook, which dispatches to the MCP
# action handler. The user clicks → server fires the action → returns a
# confirmation page. See briefing_webhook.py.
def _cowork_url(token: str) -> str:
    return briefing_webhook.action_url(token)


# Gmail deeplinks
def _gmail_thread_url(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"


def _gmail_draft_url(draft_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#drafts/{draft_id}"


# Calendar deeplinks
def _calendar_event_url(event_id: str) -> str:
    return f"https://calendar.google.com/calendar/u/0/r/event?eid={event_id}"


def _calendar_eventedit_url(event_id: str) -> str:
    return f"https://calendar.google.com/calendar/u/0/r/eventedit/{event_id}"


# Tasks deeplink — Google Tasks doesn't have a direct task URL; opens Tasks app.
_TASKS_URL = "https://tasks.google.com/u/0/"


# --------------------------------------------------------------------------- #
# Action button builders
# --------------------------------------------------------------------------- #


def build_email_actions(item: EmailItem) -> list[ActionButton]:
    """Register tokens + return the 4 action buttons for one email."""
    btns: list[ActionButton] = []
    label_subj = item.subject[:60]

    # Approve send: prefer Gmail draft URL if we staged one, else token-only path
    approve_token = briefing_actions.enqueue(
        "approve_send",
        payload={"draft_id": item.draft_id, "thread_id": item.thread_id},
        label=f"Approve send: {label_subj}",
    )
    approve_href = _gmail_draft_url(item.draft_id) if item.draft_id else _cowork_url(approve_token)
    btns.append(ActionButton(
        label="↑ Send",
        kind="approve_send",
        style="primary",
        href=approve_href,
        token=approve_token,
    ))

    schedule_token = briefing_actions.enqueue(
        "schedule_send",
        payload={"draft_id": item.draft_id, "thread_id": item.thread_id,
                 "default_send_at_local": "09:00"},
        label=f"Schedule send: {label_subj}",
    )
    btns.append(ActionButton(
        label="⏱ Later",
        kind="schedule_send",
        style="neutral",
        href=_cowork_url(schedule_token),
        token=schedule_token,
    ))

    read_token = briefing_actions.enqueue(
        "mark_read",
        payload={"thread_id": item.thread_id},
        label=f"Mark read: {label_subj}",
    )
    btns.append(ActionButton(
        label="◉ Read",
        kind="mark_read",
        style="neutral",
        href=_cowork_url(read_token),
        token=read_token,
    ))

    task_token = briefing_actions.enqueue(
        "mark_as_task",
        payload={"thread_id": item.thread_id,
                 "title": f"Reply: {label_subj}",
                 "link": _gmail_thread_url(item.thread_id)},
        label=f"Mark as task: {label_subj}",
    )
    btns.append(ActionButton(
        label="☑ Task",
        kind="mark_as_task",
        style="neutral",
        href=_cowork_url(task_token),
        token=task_token,
    ))
    return btns


def build_meeting_actions(item: MeetingItem) -> list[ActionButton]:
    btns: list[ActionButton] = []
    label_subj = item.summary[:60]

    accept_token = briefing_actions.enqueue(
        "accept_meeting",
        payload={"event_id": item.event_id, "response": "accepted"},
        label=f"Accept: {label_subj}",
    )
    btns.append(ActionButton(
        label="✓ Accept",
        kind="accept_meeting",
        style="safe",
        href=_cowork_url(accept_token),
        token=accept_token,
    ))

    decline_token = briefing_actions.enqueue(
        "decline_meeting",
        payload={"event_id": item.event_id, "response": "declined"},
        label=f"Decline: {label_subj}",
    )
    btns.append(ActionButton(
        label="✗ Decline",
        kind="decline_meeting",
        style="danger",
        href=_cowork_url(decline_token),
        token=decline_token,
    ))

    suggest_token = briefing_actions.enqueue(
        "suggest_new_time",
        payload={"event_id": item.event_id,
                 "current_start_iso": item.start_iso,
                 "current_end_iso": item.end_iso},
        label=f"Suggest new time: {label_subj}",
    )
    btns.append(ActionButton(
        label="⟲ Reschedule",
        kind="suggest_new_time",
        style="neutral",
        href=_cowork_url(suggest_token),
        token=suggest_token,
    ))
    return btns


def build_task_actions(item: TaskItem) -> list[ActionButton]:
    btns: list[ActionButton] = []
    label_subj = item.title[:60]

    complete_token = briefing_actions.enqueue(
        "complete_task",
        payload={"task_id": item.task_id, "tasklist_id": item.tasklist_id},
        label=f"Complete: {label_subj}",
    )
    btns.append(ActionButton(
        label="✓ Done",
        kind="complete_task",
        style="safe",
        href=_cowork_url(complete_token),
        token=complete_token,
    ))

    ignore_token = briefing_actions.enqueue(
        "ignore_task",
        payload={"task_id": item.task_id, "tasklist_id": item.tasklist_id},
        label=f"Ignore: {label_subj}",
    )
    btns.append(ActionButton(
        label="– Skip",
        kind="ignore_task",
        style="neutral",
        href=_cowork_url(ignore_token),
        token=ignore_token,
    ))

    schedule_token = briefing_actions.enqueue(
        "schedule_to_calendar",
        payload={"task_id": item.task_id, "tasklist_id": item.tasklist_id,
                 "title": item.title, "notes": item.notes,
                 "default_duration_min": 30},
        label=f"Schedule to calendar: {label_subj}",
    )
    btns.append(ActionButton(
        label="📅 Schedule",
        kind="schedule_to_calendar",
        style="primary",
        href=_cowork_url(schedule_token),
        token=schedule_token,
    ))
    return btns


# --------------------------------------------------------------------------- #
# Top-level composer
# --------------------------------------------------------------------------- #


def compose_briefing(
    *,
    date: str,
    greeting_name: str,
    user_email: str,
    weather_forecast: Optional[_weather.DailyForecast],
    email_items: Iterable[EmailItem],
    meeting_items: Iterable[MeetingItem],
    task_items: Iterable[TaskItem],
    news_items: Optional[Iterable[dict]] = None,
) -> ExecutiveBriefing:
    """Compose the full briefing — registers tokens, attaches actions."""
    emails = list(email_items)
    meetings = list(meeting_items)
    tasks = list(task_items)
    news = list(news_items or [])

    for e in emails:
        if not e.actions:
            e.actions = build_email_actions(e)
    for m in meetings:
        if not m.actions:
            m.actions = build_meeting_actions(m)
    for t in tasks:
        if not t.actions:
            t.actions = build_task_actions(t)

    sig_changes: list[int] = []
    if weather_forecast:
        sig_changes = _weather.detect_significant_changes(weather_forecast.hourly)

    return ExecutiveBriefing(
        date=date,
        greeting_name=greeting_name,
        user_email=user_email,
        weather=weather_forecast,
        weather_significant_change_idx=sig_changes,
        emails=emails,
        meetings=meetings,
        tasks=tasks,
        news=news,
    )


# --------------------------------------------------------------------------- #
# HTML email rendering
# --------------------------------------------------------------------------- #


def _esc(s: str) -> str:
    return _html.escape(s or "", quote=True)


def _greeting_word(now: Optional[_dt.datetime] = None) -> str:
    """Return 'morning', 'afternoon', or 'evening' based on the local hour.

    Boundaries:
      05:00–11:59 → morning
      12:00–17:59 → afternoon
      18:00–04:59 → evening
    """
    hour = (now or _dt.datetime.now()).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


def _btn(b: ActionButton) -> str:
    bg_key = f"btn_{b.style}_bg"
    fg_key = f"btn_{b.style}_text"
    bg = COLORS.get(bg_key, COLORS["btn_neutral_bg"])
    fg = COLORS.get(fg_key, COLORS["btn_neutral_text"])
    return (
        f'<a href="{_esc(b.href)}" '
        f'style="display:inline-block;margin:0 6px 6px 0;padding:8px 14px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:600;'
        f'text-decoration:none;border-radius:6px;'
        f'background:{bg};color:{fg};border:1px solid {bg};">'
        f'{_esc(b.label)}</a>'
    )


# Conditions that get a yellow highlight band on the chart background.
_PRECIP_HIGHLIGHT_CONDITIONS = {
    "rain", "heavy_rain", "drizzle", "thunderstorm",
    "snow", "sleet",
}


# Light-blue graph-paper palette (header background)
_GP_COLORS = {
    "bg":          "#eaf2fb",   # very light blue base
    "grid_minor":  "#cfdcec",   # subtle grid
    "grid_major":  "#a8bdd6",   # bolder major grid lines
    "ink":         "#0d2746",   # text + axis (deep navy)
    "ink_muted":   "#5d728d",
    "curve":       "#1a4f8c",   # temperature curve color
    "curve_fill":  "#3a72b81f", # very translucent under-curve fill
    "precip_band": "#fbe3a350", # translucent yellow for rain/snow ranges
    "marker":      "#a23a3a",   # sunrise/sunset reference line
    "ideal_band":  "#b5d8b260", # translucent light green ideal-temp zone (darker by ~5%)
    "ideal_label": "#2d6e3e",   # green ideal-zone label
    "rain_drop":   "#3a72b8",   # blue raindrop strokes
    "snow_drop":   "#8aa6c4",   # cool grey-blue snowflake dots
    "header_band": "#0d1f3a",   # dark navy header band at top of chart
    "header_text": "#ffffff",   # white time labels in the header
    "header_eyebrow": "#a8bdd6", # muted light-blue MORNING eyebrow
}


def _hours_to_x(hour_float: float, plot_x0: float, plot_x1: float) -> float:
    """Map an hour (0..24) to an X coordinate within the plot area."""
    return plot_x0 + (max(0.0, min(24.0, hour_float)) / 24.0) * (plot_x1 - plot_x0)


def _temp_to_y(temp: float, t_min: float, t_max: float,
               plot_y_top: float, plot_y_bot: float) -> float:
    """Map a temp to a Y coordinate. Higher temp = lower Y (near top)."""
    if t_max == t_min:
        return (plot_y_top + plot_y_bot) / 2
    pct = (temp - t_min) / (t_max - t_min)
    return plot_y_bot - pct * (plot_y_bot - plot_y_top)


def _smooth_path(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom-to-Bezier smoothing through the given points.
    Returns an SVG path 'd' attribute. Edge points are duplicated for
    boundary conditions so the curve naturally tapers without overshoot.
    """
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M {x:.1f},{y:.1f}"
    n = len(points)
    parts = [f"M {points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[0]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[-1]
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        parts.append(
            f"C {c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} "
            f"{p2[0]:.1f},{p2[1]:.1f}"
        )
    return " ".join(parts)


def _parse_clock_hours(s: str | None) -> float | None:
    """'06:30 AM' / '7:50 PM' → fractional hours. None if unparseable."""
    if not s:
        return None
    text = s.strip().upper()
    is_pm = "PM" in text
    is_am = "AM" in text
    text = text.replace("AM", "").replace("PM", "").strip()
    try:
        hh, mm = (int(x) for x in text.split(":"))
    except ValueError:
        return None
    if is_pm and hh != 12:
        hh += 12
    if is_am and hh == 12:
        hh = 0
    return hh + mm / 60.0


def _precip_drops_svg(condition: str, x0: float, x1: float,
                      y0: float, y1: float, drop_color: str) -> str:
    """Modern rain drops or snow flakes scattered inside a precip band.

    For rain: short vertical strokes (raindrops) at angled positions.
    For snow: small filled circles (snowflakes) in a soft grid.
    Deterministic — same band region always renders the same drop pattern.
    """
    width = x1 - x0
    height = y1 - y0
    is_snow = condition in {"snow", "sleet"}
    parts: list[str] = []
    # Grid: ~40px column spacing, 24px row spacing, alternating offsets
    col_step, row_step = 40, 24
    cols = max(1, int(width // col_step))
    rows = max(1, int(height // row_step))
    cell_w = width / cols if cols else width
    cell_h = height / rows if rows else height
    for ri in range(rows):
        offset = (cell_w / 2) if (ri % 2) else 0
        for ci in range(cols):
            cx = x0 + offset + ci * cell_w + cell_w / 2
            cy = y0 + ri * cell_h + cell_h / 2
            if cx < x0 + 4 or cx > x1 - 4:
                continue
            if cy < y0 + 4 or cy > y1 - 4:
                continue
            if is_snow:
                parts.append(
                    f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.2" '
                    f'fill="{drop_color}" opacity="0.85" />'
                )
            else:
                # Slim raindrop — short angled stroke + a small head
                parts.append(
                    f'<line x1="{cx:.1f}" y1="{cy - 5:.1f}" '
                    f'x2="{cx + 1.5:.1f}" y2="{cy + 5:.1f}" '
                    f'stroke="{drop_color}" stroke-width="1.6" '
                    f'stroke-linecap="round" opacity="0.85" />'
                )
    return "".join(parts)


def _weather_strip_html(forecast: _weather.DailyForecast,
                       sig_changes: list[int]) -> str:
    """Header (HTML) + chart (inline SVG) for the daily weather panel.

    Layout:
      • HTML title row above the blue card: 'WEATHER · location'.
      • SVG chart card spans 0000 → 2400 edge-to-edge.
      • 9 datapoints (00, 03, 06, 09, 12, 15, 18, 21, 24).
      • Per-point: time label at top of chart → outline icon → temp pill on curve.
      • Smooth Bezier curve through all 9 points.
      • Light-green 65–75°F ideal zone band (horizontal stripe).
      • Yellow tinted precipitation bands with rain drops or snowflakes.
      • Thicker red dashed sunrise/sunset reference lines, labels under graph.
      • Summary line at the very bottom.
    """
    if not forecast or not forecast.hourly:
        return ""

    location_label = forecast.location_label or ""
    summary_line = forecast.summary or ""
    sunrise = forecast.sunrise or ""
    sunset = forecast.sunset or ""

    # ---- SVG geometry --------------------------------------------------- #
    # Top: dark navy header band with white time labels (no eyebrow).
    # Bottom: thin navy footer below sunrise/sunset for symmetry.
    W, H = 880, 210
    plot_x0 = 0
    plot_x1 = W

    header_h = 32        # dark navy band height (no MORNING text)
    label_y = 21         # time labels (white) centered in band

    chart_y0 = header_h  # graph paper starts right after the header band
    chart_y1 = 168       # bottom (axis baseline)
    plot_y_top = 78      # curve top — headroom for icons above pills
    plot_y_bot = chart_y1 - 8

    sun_label_y = 184    # sunrise/sunset labels between chart + footer
    footer_y0 = 196      # thin navy footer
    footer_y1 = H        # to bottom of canvas
    # H = 210, footer 14px tall

    # ---- Datapoints (build 9 plotted points: 00, 03, ..., 21, 24) ------ #
    hourly = list(forecast.hourly)
    points: list[tuple[float, float, _weather.HourlyForecast]] = []
    for h in hourly:
        try:
            hh, mm = (int(x) for x in h.hour_local.split(":"))
        except ValueError:
            continue
        hr = hh + mm / 60.0
        points.append((hr, float(h.temp_f), h))
    if not points:
        return ""

    # Add a 9th point at 24:00 as a real plotted endpoint (mirrors 00:00).
    last_hr, last_t, last_h = points[-1]
    if last_hr < 24.0:
        first_hr, first_t, first_h = points[0]
        # End-of-day temp: blend last + first (next-day-midnight)
        end_t = (last_t + first_t) / 2
        # End-of-day condition: take the last condition (likely night)
        end_h = _weather.HourlyForecast(
            hour_local="24:00", temp_f=int(round(end_t)),
            feels_like_f=int(round(end_t - 2)),
            condition=last_h.condition,
            icon=last_h.icon, description=last_h.description,
            precip_chance_pct=0, wind_mph=last_h.wind_mph,
        )
        points.append((24.0, end_t, end_h))

    # Per-city ideal temperature range — drives the green band and the
    # axis domain so the band is always visible.
    ideal_low, ideal_high = _weather.get_ideal_range(location_label)

    # Temperature axis — force domain to include the city's ideal range so
    # the green band is always visible regardless of forecast.
    temps = [p[1] for p in points]
    t_min = min(min(temps) - 4, ideal_low - 5)
    t_max = max(max(temps) + 4, ideal_high + 5)

    xy = [(_hours_to_x(hr, plot_x0, plot_x1),
           _temp_to_y(t, t_min, t_max, plot_y_top, plot_y_bot))
          for hr, t, _ in points]
    curve_path = _smooth_path(xy)
    fill_path = curve_path + (
        f" L {xy[-1][0]:.1f},{plot_y_bot} "
        f"L {xy[0][0]:.1f},{plot_y_bot} Z"
    )

    # ---- Precip bands + drops ------------------------------------------ #
    bands: list[str] = []
    drops: list[str] = []
    for i, (hr, _, h) in enumerate(points):
        if h.condition not in _PRECIP_HIGHLIGHT_CONDITIONS:
            continue
        next_hr = points[i + 1][0] if i + 1 < len(points) else 24.0
        x0 = _hours_to_x(hr, plot_x0, plot_x1)
        x1 = _hours_to_x(next_hr, plot_x0, plot_x1)
        bands.append(
            f'<rect x="{x0:.1f}" y="{chart_y0}" '
            f'width="{(x1 - x0):.1f}" height="{chart_y1 - chart_y0}" '
            f'fill="{_GP_COLORS["precip_band"]}" />'
        )
        drop_color = (_GP_COLORS["snow_drop"]
                       if h.condition in {"snow", "sleet"}
                       else _GP_COLORS["rain_drop"])
        drops.append(_precip_drops_svg(
            h.condition, x0=x0, x1=x1,
            y0=chart_y0 + 4, y1=chart_y1 - 4,
            drop_color=drop_color,
        ))

    # ---- Ideal weather zone (per-city light green band) ---------------- #
    ideal_low_y = _temp_to_y(ideal_high, t_min, t_max, plot_y_top, plot_y_bot)
    ideal_high_y = _temp_to_y(ideal_low, t_min, t_max, plot_y_top, plot_y_bot)
    ideal_band = (
        f'<rect x="{plot_x0}" y="{ideal_low_y:.1f}" '
        f'width="{plot_x1 - plot_x0}" '
        f'height="{(ideal_high_y - ideal_low_y):.1f}" '
        f'fill="{_GP_COLORS["ideal_band"]}" />'
        # Label sits far-left, just above the upper edge of the band
        f'<text x="{plot_x0 + 12}" y="{ideal_low_y - 4:.1f}" '
        f'text-anchor="start" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="10" font-weight="700" letter-spacing="1.5" '
        f'fill="{_GP_COLORS["ideal_label"]}">'
        f'IDEAL ZONE {ideal_low}–{ideal_high}°F</text>'
    )

    # ---- Time labels (white, inside the dark navy header band) --------- #
    time_labels = []
    for hr, _t, h in points:
        x = _hours_to_x(hr, plot_x0, plot_x1)
        anchor = "start" if hr <= 0.0 else ("end" if hr >= 23.5 else "middle")
        # Slight inset for edge labels so they don't kiss the band edge.
        if anchor == "start":
            x_label = x + 8
        elif anchor == "end":
            x_label = x - 8
        else:
            x_label = x
        # Use "00:00" rather than "24:00" for the right-edge label (cleaner).
        label_text = "00:00" if hr >= 23.5 else h.hour_local
        time_labels.append(
            f'<text x="{x_label:.1f}" y="{label_y}" text-anchor="{anchor}" '
            f'font-family="Arial, Helvetica, sans-serif" '
            f'font-size="13" font-weight="700" letter-spacing="0.5" '
            f'fill="{_GP_COLORS["header_text"]}">{_esc(label_text)}</text>'
        )

    # ---- Per-datapoint: pill on curve, icon directly above pill --------- #
    markers = []
    icons_svg = []
    icon_size = 24
    pill_w, pill_h = 38, 22
    for (x, y), (hr, t, h) in zip(xy, points):
        # Pill anchored center-on-point, slid inward at edges
        pill_x = x - pill_w / 2
        if hr <= 0.0:
            pill_x = x + 2
        elif hr >= 23.5:
            pill_x = x - pill_w - 2
        markers.append(
            f'<rect x="{pill_x:.1f}" y="{y - pill_h/2:.1f}" '
            f'width="{pill_w}" height="{pill_h}" rx="11" ry="11" '
            f'fill="{_GP_COLORS["curve"]}" stroke="#ffffff" '
            f'stroke-width="2"/>'
            f'<text x="{pill_x + pill_w/2:.1f}" y="{y + 4:.1f}" '
            f'text-anchor="middle" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="12" font-weight="700" fill="#ffffff">'
            f'{int(round(t))}°</text>'
        )
        # Icon: positioned directly above the pill (vertical gap ~8px)
        icon_cx = x
        icon_y = y - pill_h / 2 - 8 - icon_size  # icon BOTTOM 8px above pill TOP
        icon_x = icon_cx - icon_size / 2
        if hr <= 0.0:
            icon_x = pill_x  # align icon left edge with pill left edge
        elif hr >= 23.5:
            icon_x = pill_x + pill_w - icon_size  # align right edges
        icons_svg.append(weather_icons.render_icon(
            h.condition, x=icon_x, y=icon_y, size=icon_size,
            color=_GP_COLORS["ink"], stroke_width=1.7,
        ))

    # ---- Sunrise + sunset (thick lines, labels under graph) ----------- #
    sun_lines = []
    sun_labels = []
    sr_h = _parse_clock_hours(sunrise)
    ss_h = _parse_clock_hours(sunset)
    if sr_h is not None:
        x = _hours_to_x(sr_h, plot_x0, plot_x1)
        sun_lines.append(
            f'<line x1="{x:.1f}" y1="{chart_y0}" '
            f'x2="{x:.1f}" y2="{chart_y1}" '
            f'stroke="{_GP_COLORS["marker"]}" stroke-width="4" '
            f'stroke-dasharray="7,5" stroke-linecap="round" '
            f'opacity="0.95" />'
        )
        sun_labels.append(
            f'<text x="{x:.1f}" y="{sun_label_y}" text-anchor="middle" '
            f'font-family="Arial, Helvetica, sans-serif" '
            f'font-size="11" font-weight="700" letter-spacing="1.2" '
            f'fill="{_GP_COLORS["marker"]}">↑ SUNRISE {_esc(sunrise)}</text>'
        )
    if ss_h is not None:
        x = _hours_to_x(ss_h, plot_x0, plot_x1)
        sun_lines.append(
            f'<line x1="{x:.1f}" y1="{chart_y0}" '
            f'x2="{x:.1f}" y2="{chart_y1}" '
            f'stroke="{_GP_COLORS["marker"]}" stroke-width="4" '
            f'stroke-dasharray="7,5" stroke-linecap="round" '
            f'opacity="0.95" />'
        )
        sun_labels.append(
            f'<text x="{x:.1f}" y="{sun_label_y}" text-anchor="middle" '
            f'font-family="Arial, Helvetica, sans-serif" '
            f'font-size="11" font-weight="700" letter-spacing="1.2" '
            f'fill="{_GP_COLORS["marker"]}">↓ SUNSET {_esc(sunset)}</text>'
        )

    # ---- Compose the SVG ----------------------------------------------- #
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"
       width="100%" preserveAspectRatio="xMidYMid meet"
       style="display:block; border-radius:10px; background:{_GP_COLORS["bg"]};">
  <defs>
    <pattern id="graph-paper-minor" width="20" height="20" patternUnits="userSpaceOnUse">
      <path d="M 20 0 L 0 0 0 20" fill="none"
            stroke="{_GP_COLORS["grid_minor"]}" stroke-width="0.5" />
    </pattern>
    <pattern id="graph-paper" width="80" height="80" patternUnits="userSpaceOnUse">
      <rect width="80" height="80" fill="url(#graph-paper-minor)"/>
      <path d="M 80 0 L 0 0 0 80" fill="none"
            stroke="{_GP_COLORS["grid_major"]}" stroke-width="1.0" />
    </pattern>
  </defs>

  <!-- Dark navy header band with white time labels -->
  <rect x="0" y="0" width="{W}" height="{header_h}"
        fill="{_GP_COLORS["header_band"]}" />
  {''.join(time_labels)}

  <!-- Graph-paper backdrop, edge-to-edge -->
  <rect x="{plot_x0}" y="{chart_y0}" width="{plot_x1 - plot_x0}"
        height="{chart_y1 - chart_y0}" fill="url(#graph-paper)" />

  <!-- Ideal weather (per-city) horizontal zone -->
  {ideal_band}

  <!-- Precipitation bands + raindrops/snowflakes -->
  {''.join(bands)}
  {''.join(drops)}

  <!-- Sunrise/sunset reference lines -->
  {''.join(sun_lines)}

  <!-- Under-curve fill + curve -->
  <path d="{fill_path}" fill="{_GP_COLORS["curve_fill"]}" stroke="none" />
  <path d="{curve_path}" fill="none"
        stroke="{_GP_COLORS["curve"]}" stroke-width="2.5"
        stroke-linejoin="round" stroke-linecap="round" />

  <!-- Outline icons above pills, then pills on curve -->
  {''.join(icons_svg)}
  {''.join(markers)}

  <!-- Bottom axis baseline -->
  <line x1="{plot_x0}" y1="{chart_y1}" x2="{plot_x1}" y2="{chart_y1}"
        stroke="{_GP_COLORS["grid_major"]}" stroke-width="1" />

  <!-- Sunrise/sunset labels under the graph -->
  {''.join(sun_labels)}

  <!-- Thin dark navy footer (mirrors the header band, frames the chart) -->
  <rect x="0" y="{footer_y0}" width="{W}" height="{footer_y1 - footer_y0}"
        fill="{_GP_COLORS["header_band"]}" />
</svg>'''

    # HTML title row above the chart — eyebrow + city + summary inline.
    header_row = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'width="100%" style="margin:18px 0 10px 0;">'
        f'<tr>'
        f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'font-weight:700;color:{COLORS["accent"]};text-transform:uppercase;'
        f'letter-spacing:2px;padding-bottom:4px;">WEATHER &middot; '
        f'<span style="color:{COLORS["text"]};font-size:13px;'
        f'letter-spacing:0;text-transform:none;">'
        f'{_esc(location_label)}</span></td>'
        f'</tr>'
        f'<tr>'
        f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:500;color:{COLORS["muted"]};">'
        f'{_esc(summary_line)}</td>'
        f'</tr></table>'
    )

    return (
        f'{header_row}'
        f'<div style="margin-bottom:18px;border-radius:10px;overflow:hidden;'
        f'background:{_GP_COLORS["bg"]};">{svg}</div>'
    )


# --------------------------------------------------------------------------- #
# Real CSS tabs — radio + label trick.
#
# Apple Mail / browser preview / Cowork inbox webview support the :checked
# pseudo-class, so only the active tab's panel is visible at a time.
#
# Gmail web/app strips form elements — it'll show all 3 panels stacked, which
# is fine: the user still gets every section.
# --------------------------------------------------------------------------- #


_TAB_STYLE_BLOCK = """\
<style>
  /* Inverted segmented-control tabs — active = filled navy, inactive = muted. */
  .standup-tabs input[type=radio] { display: none; }
  .standup-tabs .tab-labels {
    display: flex; gap: 4px; margin: 14px 0 0;
    padding: 4px;
    background: #eef0f3; border-radius: 9px;
  }
  .standup-tabs .tab-labels label {
    flex: 1; cursor: pointer;
    padding: 9px 14px; text-align: center;
    background: transparent;
    color: #6a7079;
    font: 700 12px/1 Arial, Helvetica, sans-serif;
    letter-spacing: 1.4px; text-transform: uppercase;
    border-radius: 6px;
    transition: background .12s ease, color .12s ease;
  }
  .standup-tabs .tab-labels label:hover {
    color: #1a4f8c; background: rgba(26, 79, 140, 0.06);
  }
  .standup-tabs .tab-labels label .count {
    display: inline-block; margin-left: 6px; padding: 1px 7px;
    background: #d6dde6; border-radius: 8px;
    color: #6a7079; letter-spacing: 0;
    font-size: 10px; font-weight: 700;
  }
  .standup-tabs .tab-panel { display: none; padding: 16px 0 0; }
  /* Active tab — filled navy with white text */
  .standup-tabs #tab-emails:checked   ~ .tab-labels label[for=tab-emails],
  .standup-tabs #tab-meetings:checked ~ .tab-labels label[for=tab-meetings],
  .standup-tabs #tab-tasks:checked    ~ .tab-labels label[for=tab-tasks],
  .standup-tabs #tab-news:checked     ~ .tab-labels label[for=tab-news] {
    background: #1a4f8c; color: #ffffff;
  }
  .standup-tabs #tab-emails:checked   ~ .tab-labels label[for=tab-emails] .count,
  .standup-tabs #tab-meetings:checked ~ .tab-labels label[for=tab-meetings] .count,
  .standup-tabs #tab-tasks:checked    ~ .tab-labels label[for=tab-tasks] .count,
  .standup-tabs #tab-news:checked     ~ .tab-labels label[for=tab-news] .count {
    background: rgba(255, 255, 255, 0.22); color: #ffffff;
  }
  .standup-tabs #tab-emails:checked   ~ .panels #panel-emails,
  .standup-tabs #tab-meetings:checked ~ .panels #panel-meetings,
  .standup-tabs #tab-tasks:checked    ~ .panels #panel-tasks,
  .standup-tabs #tab-news:checked     ~ .panels #panel-news {
    display: block;
  }
  /* Section titles inside panels — subtle, no underline rules */
  .standup-tabs .panel-header {
    font: 700 11px/1 Arial, Helvetica, sans-serif;
    color: #6a7079; text-transform: uppercase; letter-spacing: 1.5px;
    margin: 4px 0 14px; padding: 0;
    display: flex; justify-content: space-between;
  }
  .panel-header .count { color: #909499; font-weight: 600; letter-spacing: 0; }
</style>
"""


def _panel_header_html(title: str, count: int) -> str:
    return (
        f'<div class="panel-header"><span>{_esc(title)}</span>'
        f'<span class="count">{count} item(s)</span></div>'
    )


def _section_header_html(title: str, count: int) -> str:
    """Legacy fallback section header — kept for non-tab contexts."""
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="margin:24px 0 10px 0;">'
        f'<tr>'
        f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:700;color:{COLORS["accent"]};text-transform:uppercase;'
        f'letter-spacing:1.5px;border-bottom:2px solid {COLORS["accent"]};'
        f'padding-bottom:6px;">{_esc(title)}</td>'
        f'<td align="right" style="font-family:Arial,Helvetica,sans-serif;'
        f'font-size:13px;color:{COLORS["muted"]};border-bottom:2px solid '
        f'{COLORS["accent"]};padding-bottom:6px;">{count} item(s)</td>'
        f'</tr></table>'
    )


def _initials(name: str) -> str:
    """Two-letter avatar initials from a sender name."""
    if not name:
        return "·"
    parts = [p for p in name.replace("\"", "").replace("'", "").split() if p]
    if not parts:
        return name[:1].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def _avatar_color(seed: str) -> str:
    """Stable accent color for the sender avatar — pulled from a small palette."""
    palette = ["#1a4f8c", "#2d6e3e", "#a23a3a", "#9a6b00", "#5a3a8c", "#0d6470"]
    if not seed:
        return palette[0]
    idx = sum(ord(c) for c in seed) % len(palette)
    return palette[idx]


def _attachment_chip(att: EmailAttachment) -> str:
    """Pill-style attachment chip — opens in a new window when clicked."""
    size_str = ""
    if att.size_bytes:
        if att.size_bytes >= 1024 * 1024:
            size_str = f" · {att.size_bytes / 1024 / 1024:.1f}MB"
        elif att.size_bytes >= 1024:
            size_str = f" · {att.size_bytes / 1024:.0f}KB"
    return (
        f'<a href="{_esc(att.url)}" target="_blank" '
        f'rel="noopener" '
        f'style="display:inline-block;margin:0 6px 6px 0;'
        f'padding:6px 12px;border:1px solid {COLORS["border"]};'
        f'border-radius:14px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:11px;color:{COLORS["text"]};background:#f6f7f9;'
        f'text-decoration:none;">'
        f'📎 {_esc(att.name)}{_esc(size_str)}'
        f'</a>'
    )


def _email_card_html(e: EmailItem) -> str:
    """Polished email card with avatar, View Original link, attachments,
    inline-editable draft, and icon-forward action buttons.

    The card is a <form> POSTing to the local webhook, so editing the
    draft and clicking Send actually round-trips and updates the Gmail
    draft before sending. Apple Mail / browser preview / Cowork webview
    honor the form; Gmail strips form tags but the visual still reads.
    """
    # Find the approve_send token so the form posts with it
    approve_btn = next((b for b in e.actions if b.kind == "approve_send"), None)
    other_btns = [b for b in e.actions if b.kind != "approve_send"]
    approve_token = approve_btn.token if approve_btn else ""
    approve_href = approve_btn.href if approve_btn else "#"

    other_btns_html = "".join(_btn(b) for b in other_btns)

    approve_label = approve_btn.label if approve_btn else "↑ Send"
    submit_btn = (
        f'<button type="submit" '
        f'style="display:inline-block;margin:0 6px 6px 0;padding:10px 18px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;'
        f'border:1px solid {COLORS["btn_primary_bg"]};border-radius:6px;'
        f'background:{COLORS["btn_primary_bg"]};color:{COLORS["btn_primary_text"]};'
        f'cursor:pointer;letter-spacing:0.3px;">'
        f'{_esc(approve_label)}</button>'
    )

    # Avatar (initials block)
    sender_label = e.sender_name or e.sender_email
    avatar_color = _avatar_color(sender_label)
    initials = _initials(sender_label)
    avatar_html = (
        f'<div style="width:40px;height:40px;background:{avatar_color};'
        f'border-radius:50%;color:#ffffff;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'font-weight:700;letter-spacing:1px;text-align:center;'
        f'line-height:40px;">{_esc(initials)}</div>'
    )

    # View Original link (Gmail thread deeplink)
    view_original_url = f"https://mail.google.com/mail/u/0/#inbox/{_esc(e.thread_id)}"
    view_original_link = (
        f'<a href="{view_original_url}" target="_blank" rel="noopener" '
        f'style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'color:{COLORS["accent"]};text-decoration:none;font-weight:600;'
        f'letter-spacing:0.3px;">↗ View original</a>'
    )

    # Attachments row
    attachments_html = ""
    if e.attachments:
        chips = "".join(_attachment_chip(a) for a in e.attachments)
        attachments_html = (
            f'<div style="margin:0 0 14px 0;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:10px;color:{COLORS["muted"]};text-transform:uppercase;'
            f'letter-spacing:1px;font-weight:700;">'
            f'<span style="margin-right:8px;">{len(e.attachments)} '
            f'attachment{"" if len(e.attachments) == 1 else "s"}</span></div>'
            f'<div style="margin-bottom:14px;">{chips}</div>'
        )

    received_html = ""
    if e.received_at:
        # Show a rough "x ago" stamp (or just date)
        received_html = (
            f'<span style="font-family:Arial,Helvetica,sans-serif;'
            f'font-size:11px;color:{COLORS["muted"]};margin-left:10px;">'
            f'· {_esc((e.received_at or "")[:16].replace("T", " "))}</span>'
        )

    backup_link = (
        f'<a href="{_esc(approve_href)}" style="display:none;">approve</a>'
    )
    webhook_url = f"{briefing_webhook.url()}/briefing/action"

    return (
        f'<form action="{_esc(webhook_url)}" method="post" '
        f'style="margin:0 0 18px 0;display:block;">'
        f'<input type="hidden" name="token" value="{_esc(approve_token)}">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'width="100%" style="background:{COLORS["card_bg"]};'
        f'border:1px solid {COLORS["border"]};border-radius:12px;'
        f'box-shadow:0 1px 2px rgba(0,0,0,0.03);">'
        # Sender row: avatar | name+email+timestamp+view-original
        f'<tr><td style="padding:16px 20px 0 20px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f'<tr>'
        f'<td valign="top" width="48" style="padding-right:12px;">{avatar_html}</td>'
        f'<td valign="top">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:700;color:{COLORS["text"]};line-height:1.3;">'
        f'{_esc(sender_label)}{received_html}</div>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'color:{COLORS["muted"]};margin-top:1px;">'
        f'{_esc(e.sender_email)}</div>'
        f'</td>'
        f'<td valign="top" align="right" style="padding-left:8px;">'
        f'{view_original_link}</td>'
        f'</tr></table></td></tr>'
        # Subject line
        f'<tr><td style="padding:14px 20px 0 20px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;'
        f'font-weight:700;color:{COLORS["text"]};line-height:1.3;">'
        f'{_esc(e.subject or "(no subject)")}</div>'
        f'</td></tr>'
        # Snippet
        f'<tr><td style="padding:6px 20px 14px 20px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'color:{COLORS["muted"]};line-height:1.55;">{_esc(e.snippet or "")}'
        f'</div></td></tr>'
        # Attachments (optional)
        + (f'<tr><td style="padding:0 20px;">{attachments_html}</td></tr>'
           if attachments_html else "")
        # Drafted reply (editable)
        + f'<tr><td style="padding:0 20px 6px 20px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;'
        f'text-transform:uppercase;letter-spacing:1.4px;'
        f'color:{COLORS["accent"]};font-weight:700;margin-bottom:6px;">'
        f'DRAFTED REPLY · EDIT BEFORE SENDING</div>'
        f'<textarea name="body_override" rows="10" '
        f'style="display:block;width:100%;box-sizing:border-box;'
        f'padding:12px 14px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:13px;line-height:1.5;color:{COLORS["text"]};'
        f'background:{COLORS["accent_lt"]};border:1px solid {COLORS["border"]};'
        f'border-left:3px solid {COLORS["accent"]};border-radius:0 6px 6px 0;'
        f'resize:vertical;outline:none;'
        f'min-height:200px;max-height:1560px;overflow-y:auto;">'
        f'{_esc(e.drafted_reply or "")}'
        f'</textarea>'
        f'</td></tr>'
        # Action buttons row
        f'<tr><td style="padding:14px 20px 18px 20px;'
        f'border-top:1px solid {COLORS["border"]};">'
        f'{submit_btn}{other_btns_html}'
        f'{backup_link}'
        f'</td></tr>'
        f'</table>'
        f'</form>'
    )


def _fmt_iso_time(iso: str) -> str:
    """Format an ISO timestamp like '2026-04-29T09:00:00-07:00' → '9:00 AM'.

    Falls back to the empty string if the input can't be parsed.
    """
    if not iso:
        return ""
    try:
        # Normalize "Z" to "+00:00" for fromisoformat compatibility.
        dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    hour = dt.hour
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    if dt.minute == 0:
        return f"{h12}:00 {suffix}"
    return f"{h12}:{dt.minute:02d} {suffix}"


def _meeting_color(m: MeetingItem) -> str:
    """Pick a calendar-event color based on meeting context."""
    title = (m.summary or "").lower()
    if "1:1" in title or "1-1" in title:
        return "#3a72b8"   # blue — 1:1
    if "demo" in title or "customer" in title or "client" in title:
        return "#2d6e3e"   # green — customer-facing
    if "board" in title or "review" in title or "exec" in title:
        return "#a23a3a"   # red — board / exec
    if "standup" in title or "sync" in title or "team" in title:
        return "#7b5cd6"   # purple — team
    return COLORS["accent"]  # navy — default


def _meeting_gap_html(prev_end_iso: str, next_start_iso: str) -> str:
    """Render a subtle 'X min gap' marker between back-to-back events."""
    if not prev_end_iso or not next_start_iso:
        return ""
    try:
        a = _dt.datetime.fromisoformat(prev_end_iso.replace("Z", "+00:00"))
        b = _dt.datetime.fromisoformat(next_start_iso.replace("Z", "+00:00"))
        # Normalize naive ↔ aware mismatches by attaching local tz to either.
        # (All-day events come back as naive 'YYYY-MM-DD' strings.)
        local_tz = _dt.datetime.now().astimezone().tzinfo
        if a.tzinfo is None:
            a = a.replace(tzinfo=local_tz)
        if b.tzinfo is None:
            b = b.replace(tzinfo=local_tz)
        delta_min = int((b - a).total_seconds() // 60)
    except (ValueError, TypeError):
        return ""
    if delta_min < 10:
        label = "Back-to-back"
    elif delta_min < 60:
        label = f"{delta_min} min gap"
    else:
        h = delta_min // 60
        rem = delta_min % 60
        label = f"{h}h {rem}m gap" if rem else f"{h}h gap"
    return (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;'
        f'color:{COLORS["muted"]};text-transform:uppercase;letter-spacing:1.4px;'
        f'padding:0 0 0 86px;margin:-6px 0 8px 0;">{label}</div>'
    )


def _meeting_card_html(m: MeetingItem) -> str:
    """Render a single meeting as a calendar-style event block.

    Layout (left to right):
      ┌──────┬─┬───────────────────────────────────────────┐
      │ TIME │ │ Title  YOU HOST                           │
      │ rail │ │ 📍 Location · 👥 4 attendees              │
      │      │ │ [actions]                                  │
      └──────┴─┴───────────────────────────────────────────┘
    """
    btns_html = "".join(_btn(b) for b in m.actions)
    accent = _meeting_color(m)
    organizer_badge = ""
    if m.is_organizer:
        organizer_badge = (
            f' <span style="display:inline-block;background:{COLORS["accent_lt"]};'
            f'color:{COLORS["accent"]};font-size:10px;padding:2px 6px;'
            f'border-radius:4px;font-weight:700;letter-spacing:0.5px;">YOU HOST</span>'
        )
    end_label = _fmt_iso_time(m.end_iso)
    time_block = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;'
        f'font-weight:700;color:{COLORS["text"]};line-height:1.1;">'
        f'{_esc(m.start_label)}</div>'
        + (
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
            f'color:{COLORS["muted"]};margin-top:2px;letter-spacing:0.3px;">'
            f'to {_esc(end_label)}</div>'
            if end_label else ""
        )
    )
    # Location + attendees on a single info line, separated by middle dots
    info_parts = []
    if m.location:
        info_parts.append(f'📍 {_esc(m.location)}')
    if m.attendee_count:
        plural = "s" if m.attendee_count != 1 else ""
        info_parts.append(f'👥 {m.attendee_count} attendee{plural}')
    info_html = ""
    if info_parts:
        info_html = (
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
            f'color:{COLORS["muted"]};margin-bottom:10px;line-height:1.5;">'
            + ' &nbsp;·&nbsp; '.join(info_parts)
            + '</div>'
        )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'width="100%" style="background:{COLORS["card_bg"]};'
        f'border:1px solid {COLORS["border"]};border-left:5px solid {accent};'
        f'border-radius:8px;margin-bottom:10px;">'
        f'<tr>'
        # TIME RAIL
        f'<td valign="top" width="86" '
        f'style="padding:14px 14px 14px 18px;border-right:1px dashed {COLORS["border"]};">'
        f'{time_block}</td>'
        # EVENT BODY
        f'<td valign="top" style="padding:14px 18px 12px 18px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'font-weight:700;color:{COLORS["text"]};margin-bottom:6px;line-height:1.3;">'
        f'{_esc(m.summary or "(no title)")}{organizer_badge}</div>'
        f'{info_html}'
        f'<div>{btns_html}</div>'
        f'</td>'
        f'</tr></table>'
    )


def _meetings_panel_body(meetings: list[MeetingItem]) -> str:
    """Render the full meetings list with inter-event gap markers."""
    if not meetings:
        return _empty_panel_html("No meetings today.")
    pieces: list[str] = []
    prev_end: Optional[str] = None
    for m in meetings:
        if prev_end:
            pieces.append(_meeting_gap_html(prev_end, m.start_iso))
        pieces.append(_meeting_card_html(m))
        prev_end = m.end_iso
    return "".join(pieces)


def _task_card_html(t: TaskItem) -> str:
    btns_html = "".join(_btn(b) for b in t.actions)
    due_html = ""
    if t.due_iso:
        due_label = (t.due_iso or "")[:10]
        due_html = (
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
            f'color:{COLORS["muted"]};margin-bottom:8px;">Due {_esc(due_label)}</div>'
        )
    notes_html = ""
    if t.notes:
        notes_html = (
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
            f'color:{COLORS["muted"]};font-style:italic;margin-bottom:12px;'
            f'line-height:1.5;">{_esc(t.notes)}</div>'
        )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'width="100%" style="background:{COLORS["card_bg"]};'
        f'border:1px solid {COLORS["border"]};border-radius:10px;'
        f'margin-bottom:18px;">'
        f'<tr><td style="padding:18px 20px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'font-weight:700;color:{COLORS["text"]};margin-bottom:6px;">'
        f'{_esc(t.title)}</div>'
        f'{due_html}'
        f'{notes_html}'
        f'<div>{btns_html}</div>'
        f'</td></tr></table>'
    )


def render_email_html(brief: ExecutiveBriefing) -> str:
    """Render the full briefing as a self-contained HTML email body."""
    weather_html = (
        _weather_strip_html(brief.weather, brief.weather_significant_change_idx)
        if brief.weather else ""
    )

    header_html = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin-bottom:16px;">'
        f'<tr><td>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'text-transform:uppercase;letter-spacing:2px;color:{COLORS["muted"]};">'
        f'EXECUTIVE BRIEF · {_esc(brief.date)}</div>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:26px;'
        f'font-weight:700;color:{COLORS["text"]};margin-top:4px;">'
        f'Good {_greeting_word().capitalize()}, {_esc(brief.greeting_name)}.</div>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'color:{COLORS["muted"]};margin-top:4px;">'
        f'{_esc(brief.summary_line())}</div>'
        f'</td></tr></table>'
    )

    # Three-tab layout. Each panel has its own panel-header + cards.
    # Apple Mail / browser preview honors the radio + label CSS so only the
    # active tab's panel is visible. Gmail strips form elements — the
    # `.no-tabs` JS shim below adds that class so all panels show stacked
    # in clients that don't support tabs.
    email_count = len(brief.emails)
    meeting_count = len(brief.meetings)
    task_count = len(brief.tasks)

    email_panel = (
        f'<div id="panel-emails" class="tab-panel">'
        f'{_panel_header_html("EMAIL TRIAGE — ZERO INBOX", email_count)}'
        f'{"".join(_email_card_html(e) for e in brief.emails) if brief.emails else _empty_panel_html("Inbox is clean.")}'
        f'</div>'
    )
    meetings_title = "TODAY'S MEETINGS"
    meeting_panel = (
        f'<div id="panel-meetings" class="tab-panel">'
        f'{_panel_header_html(meetings_title, meeting_count)}'
        f'{_meetings_panel_body(brief.meetings)}'
        f'</div>'
    )
    task_panel = (
        f'<div id="panel-tasks" class="tab-panel">'
        f'{_panel_header_html("ACTIVE TASKS", task_count)}'
        f'{"".join(_task_card_html(t) for t in brief.tasks) if brief.tasks else _empty_panel_html("Task list is empty.")}'
        f'</div>'
    )
    news_count = len(brief.news)
    news_panel = (
        f'<div id="panel-news" class="tab-panel">'
        f'{_panel_header_html("WORLD NEWS", news_count)}'
        f'{_news_panel_body(brief.news)}'
        f'</div>'
    )

    tabs_html = (
        f'<div class="standup-tabs">'
        f'<input type="radio" name="standup_tab" id="tab-emails" checked>'
        f'<input type="radio" name="standup_tab" id="tab-meetings">'
        f'<input type="radio" name="standup_tab" id="tab-tasks">'
        f'<input type="radio" name="standup_tab" id="tab-news">'
        f'<div class="tab-labels">'
        f'<label for="tab-emails">Email <span class="count">{email_count}</span></label>'
        f'<label for="tab-meetings">Meetings <span class="count">{meeting_count}</span></label>'
        f'<label for="tab-tasks">Tasks <span class="count">{task_count}</span></label>'
        f'<label for="tab-news">News <span class="count">{news_count}</span></label>'
        f'</div>'
        f'<div class="panels">{email_panel}{meeting_panel}{task_panel}{news_panel}</div>'
        f'</div>'
    )

    # Tiny script: if running in a browser/viewer that runs JS, leave the
    # default behavior alone. If JS is stripped (most email clients) but
    # CSS :checked works (Apple Mail), tabs work natively. If JS is stripped
    # AND CSS :checked is stripped (Gmail web), the .no-tabs class never
    # gets added — but the radios are also stripped, so the
    # standup-tabs.no-tabs fallback never applies. Result: Gmail simply shows
    # all 3 panels stacked, which is the desired graceful degradation since
    # the .panels container has no display:none default.
    fallback_css = (
        '<style>'
        '/* Gmail/Outlook fallback: when CSS :checked is stripped, force '
        '   all panels visible so nothing is hidden. */'
        '.standup-tabs .panels > .tab-panel { display: block; margin-top: 12px; '
        '   border-radius: 8px; }'
        '/* Browsers that DO support :checked override this rule above. */'
        '@supports (selector(:checked)) {'
        '  .standup-tabs .panels > .tab-panel { display: none; }'
        '  .standup-tabs #tab-emails:checked ~ .panels #panel-emails,'
        '  .standup-tabs #tab-meetings:checked ~ .panels #panel-meetings,'
        '  .standup-tabs #tab-tasks:checked ~ .panels #panel-tasks,'
        '  .standup-tabs #tab-news:checked ~ .panels #panel-news {'
        '    display: block;'
        '  }'
        '}'
        '</style>'
    )

    # v0.9.3+ — schedule link in the footer. URL is set after the operator
    # runs workflow_cron_publish_to_drive once; the URL persists in
    # config.cron_manager.schedule_url. When unset, render a setup hint
    # instead so the footer stays useful without breaking.
    schedule_url = ""
    try:
        import config as _cfg
        block = _cfg.get("cron_manager", {}) or {}
        schedule_url = (block.get("schedule_url") or "").strip()
    except Exception:
        schedule_url = ""
    if schedule_url:
        schedule_link_html = (
            f'<a href="{_esc(schedule_url)}" '
            f'style="color:{COLORS["muted"]};text-decoration:underline;">'
            f'Manage daily schedule</a>'
            f' &middot; '
            f'<span style="color:{COLORS["muted"]};">'
            f'or ask Claude: "open the cron manager"'
            f'</span>'
        )
    else:
        schedule_link_html = (
            f'<span style="color:{COLORS["muted"]};">'
            f'Manage schedule: ask Claude "open the cron manager" '
            f'(publish a shareable link with workflow_cron_publish_to_drive)'
            f'</span>'
        )

    footer_html = (
        f'<div style="margin-top:30px;padding-top:14px;'
        f'border-top:1px solid {COLORS["border"]};'
        f'font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'color:{COLORS["muted"]};line-height:1.6;">'
        f'CoAssisted Workspace &middot; Executive Briefing v0.7'
        f'<br />'
        f'{schedule_link_html}'
        f'</div>'
    )

    # News is now its own tab — full-width tabs span the entire panel.
    body = (
        f'{_TAB_STYLE_BLOCK}{fallback_css}'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="background:{COLORS["bg"]};padding:0;">'
        f'<tr><td align="center" style="padding:0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'style="width:100%;max-width:1180px;background:{COLORS["bg"]};"'
        f'><tr><td style="padding:14px 12px;">'
        f'{header_html}{weather_html}{tabs_html}'
        f'{footer_html}'
        f'</td></tr></table>'
        f'</td></tr></table>'
    )
    return body


def _empty_panel_html(message: str) -> str:
    return (
        f'<div style="padding:30px 16px;text-align:center;'
        f'color:{COLORS["muted"]};font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;font-style:italic;">{_esc(message)}</div>'
    )


def _email_grid_html(emails: list[EmailItem]) -> str:
    """Lay emails out in a 2-column grid.

    Each row has up to 2 cards side-by-side. If the count is odd, the last
    row's right cell stays empty. Mobile clients without table-layout
    support will visually fall back to stacked cards.
    """
    if not emails:
        return ""
    rows: list[str] = []
    for i in range(0, len(emails), 2):
        left = _email_card_html(emails[i])
        right = _email_card_html(emails[i + 1]) if i + 1 < len(emails) else ""
        rows.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'width="100%" style="margin:0;"><tr>'
            f'<td valign="top" width="50%" style="padding-right:9px;">{left}</td>'
            f'<td valign="top" width="50%" style="padding-left:9px;">{right}</td>'
            f'</tr></table>'
        )
    return "".join(rows)


# --------------------------------------------------------------------------- #
# News column (right sidebar)
# --------------------------------------------------------------------------- #


def _news_thumb_html(item: dict, size: int = 64) -> str:
    """Small left-side thumbnail for the minimalist news row.

    Uses the article's actual image when `thumb_url` is provided, falls
    back to a soft-color tile with source initials when no image is on file.
    """
    thumb_url = item.get("thumb_url")
    color = item.get("thumb_color") or COLORS["accent"]
    if thumb_url:
        return (
            f'<img src="{_esc(thumb_url)}" alt="" width="{size}" height="{size}" '
            f'style="display:block;width:{size}px;height:{size}px;'
            f'object-fit:cover;border-radius:6px;background:{color};" />'
        )
    initials = "".join(
        w[0] for w in (item.get("source") or "?").split()[:2]
    ).upper() or "·"
    return (
        f'<div style="width:{size}px;height:{size}px;background:{color};'
        f'border-radius:6px;color:#ffffff;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:18px;'
        f'font-weight:700;letter-spacing:1.5px;text-align:center;'
        f'line-height:{size}px;">{_esc(initials)}</div>'
    )


def _news_card_html(item: dict) -> str:
    """Minimalist horizontal list row: thumb on left, headline+source+snippet
    on right, subtle bottom divider. Whole row is a clickable link."""
    title = _esc(item.get("title") or "")
    url = _esc(item.get("url") or "#")
    source = _esc(item.get("source") or "")
    snippet = _esc(item.get("snippet") or "")
    thumb = _news_thumb_html(item)
    return (
        f'<a href="{url}" target="_blank" '
        f'style="text-decoration:none;color:inherit;display:block;'
        f'padding:12px 0;border-bottom:1px solid {COLORS["border"]};">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f'<tr>'
        f'<td valign="top" width="64" style="padding-right:12px;">{thumb}</td>'
        f'<td valign="top">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:9px;'
        f'color:{COLORS["muted"]};text-transform:uppercase;letter-spacing:1.2px;'
        f'font-weight:700;margin-bottom:2px;">{source}</div>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
        f'font-weight:600;color:{COLORS["text"]};line-height:1.35;'
        f'margin-bottom:4px;">{title}</div>'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'color:{COLORS["muted"]};line-height:1.4;">{snippet}</div>'
        f'</td></tr></table></a>'
    )


def _news_column_html(items: list[dict]) -> str:
    """Render the World News right column — minimalist (legacy sidebar layout)."""
    if not items:
        return ""
    header = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'font-weight:700;color:{COLORS["muted"]};text-transform:uppercase;'
        f'letter-spacing:1.6px;padding-bottom:8px;'
        f'border-bottom:1px solid {COLORS["border"]};margin-bottom:0;">'
        f'World News</div>'
    )
    cards = "".join(_news_card_html(it) for it in items)
    return header + cards


def _news_panel_body(items: list[dict]) -> str:
    """Render the news tab body as a 2-up grid of news cards.

    Falls back to an empty-state when no items are present. Mobile clients
    that don't honor table layout will stack the rows naturally.
    """
    if not items:
        return _empty_panel_html("No news right now.")
    rows: list[str] = []
    for i in range(0, len(items), 2):
        left = _news_card_html(items[i])
        right = _news_card_html(items[i + 1]) if i + 1 < len(items) else ""
        rows.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'width="100%" style="margin:0;"><tr>'
            f'<td valign="top" width="50%" style="padding-right:10px;">{left}</td>'
            f'<td valign="top" width="50%" style="padding-left:10px;">{right}</td>'
            f'</tr></table>'
        )
    return "".join(rows)
