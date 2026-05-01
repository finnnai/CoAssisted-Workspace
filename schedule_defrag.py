# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Schedule defrag — find fragmented gaps between meetings worth consolidating.

Pure-logic core. Wrapper at tools/schedule_defrag.py fetches calendar.

Definitions:
  - "Working hours": configurable window per day (default 8am-6pm local).
  - "Gap": continuous free time between two booked events.
  - "Fragment": a gap below MIN_USEFUL_BLOCK (default 45 min) that
    interrupts what could otherwise be a longer focus block if the
    surrounding meetings could shift.
  - "Defrag pair": two fragments on the same day where moving either
    one of the surrounding meetings would yield a single ≥ MIN_USEFUL_BLOCK
    contiguous block.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Iterable

# Defaults — tunable via config.
WORKING_HOURS_START = 8     # 8am
WORKING_HOURS_END = 18      # 6pm
MIN_USEFUL_BLOCK_MIN = 45   # below this, gap is a "fragment"
MAX_FRAGMENT_MIN = 30       # gaps strictly below this are "interruptions"


@dataclass
class Fragment:
    """One small gap between two booked events."""
    day: str               # YYYY-MM-DD
    start: _dt.datetime
    end: _dt.datetime
    duration_min: int
    before_event: str | None    # title of the event ending at `start`
    after_event: str | None     # title of the event starting at `end`

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_min": self.duration_min,
            "before_event": self.before_event,
            "after_event": self.after_event,
        }


@dataclass
class DefragSuggestion:
    """A pair of fragments that could merge into a useful focus block if
    the meeting between them moved."""
    day: str
    fragment_a: Fragment
    fragment_b: Fragment
    middle_event: str       # the meeting whose movement would unlock the merge
    middle_event_id: str | None
    if_moved_block_min: int  # the contiguous block size if we collapsed the middle

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "fragment_a": self.fragment_a.to_dict(),
            "fragment_b": self.fragment_b.to_dict(),
            "middle_event": self.middle_event,
            "middle_event_id": self.middle_event_id,
            "if_moved_block_min": self.if_moved_block_min,
        }


@dataclass
class DefragReport:
    fragments: list[Fragment] = field(default_factory=list)
    suggestions: list[DefragSuggestion] = field(default_factory=list)
    days_analyzed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "days_analyzed": list(self.days_analyzed),
            "fragment_count": len(self.fragments),
            "suggestion_count": len(self.suggestions),
            "fragments": [f.to_dict() for f in self.fragments],
            "suggestions": [s.to_dict() for s in self.suggestions],
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _parse_event(e: dict) -> tuple[_dt.datetime, _dt.datetime, str, str | None] | None:
    """Pull (start_dt, end_dt, summary, event_id) out of a Calendar API event.
    Returns None for all-day events or unparseable items."""
    s = e.get("start") or {}
    f = e.get("end") or {}
    s_iso = s.get("dateTime")
    f_iso = f.get("dateTime")
    if not s_iso or not f_iso:
        return None
    try:
        sd = _dt.datetime.fromisoformat(s_iso)
        fd = _dt.datetime.fromisoformat(f_iso)
    except ValueError:
        return None
    return sd, fd, e.get("summary") or "(no title)", e.get("id")


def _working_window(day: _dt.date, tz: _dt.tzinfo,
                    start_h: int, end_h: int) -> tuple[_dt.datetime, _dt.datetime]:
    return (
        _dt.datetime(day.year, day.month, day.day, start_h, tzinfo=tz),
        _dt.datetime(day.year, day.month, day.day, end_h, tzinfo=tz),
    )


# --------------------------------------------------------------------------- #
# Core analysis
# --------------------------------------------------------------------------- #


def find_fragments(
    events: Iterable[dict],
    *,
    working_start_h: int = WORKING_HOURS_START,
    working_end_h: int = WORKING_HOURS_END,
    min_useful_block_min: int = MIN_USEFUL_BLOCK_MIN,
    max_fragment_min: int = MAX_FRAGMENT_MIN,
) -> DefragReport:
    """Scan a list of calendar events for fragmented gaps + defrag suggestions.

    Args:
        events: Calendar API event dicts. Must have start.dateTime/end.dateTime.
        working_start_h, working_end_h: hour bounds for "working day".
        min_useful_block_min: below this, a gap is a fragment.
        max_fragment_min: only gaps STRICTLY below this trigger a fragment;
                          gaps between [max_fragment_min, min_useful_block_min)
                          are also flagged but rated lower.

    Returns:
        DefragReport with all detected fragments + paired defrag suggestions.
    """
    # Group events by date in their own tz.
    by_day: dict[str, list[tuple[_dt.datetime, _dt.datetime, str, str | None]]] = {}
    for e in events:
        parsed = _parse_event(e)
        if not parsed:
            continue
        sd, _, _, _ = parsed
        day_key = sd.date().isoformat()
        by_day.setdefault(day_key, []).append(parsed)

    days_analyzed = sorted(by_day.keys())
    fragments: list[Fragment] = []
    suggestions: list[DefragSuggestion] = []

    for day in days_analyzed:
        evs = sorted(by_day[day], key=lambda x: x[0])
        if not evs:
            continue
        tz = evs[0][0].tzinfo
        if tz is None:
            continue
        wd_start, wd_end = _working_window(
            evs[0][0].date(), tz, working_start_h, working_end_h,
        )

        # Walk pairwise gaps, including from working-start to first event
        # and from last event to working-end.
        cursor = wd_start
        gap_list: list[tuple[_dt.datetime, _dt.datetime, str | None, str | None, str | None]] = []
        # Gaps look like (gap_start, gap_end, prev_event_title, next_event_title, next_event_id)

        prev_title: str | None = None

        for sd, fd, title, ev_id in evs:
            if sd < cursor:
                # Overlapping events — skip the gap calc, keep advancing.
                cursor = max(cursor, fd)
                prev_title = title
                continue
            gap_start = max(cursor, wd_start)
            gap_end = min(sd, wd_end)
            if gap_end > gap_start and gap_start < wd_end:
                gap_list.append((gap_start, gap_end, prev_title, title, ev_id))
            cursor = fd
            prev_title = title

        # Trailing gap from last event to end-of-day.
        if cursor < wd_end:
            gap_list.append((cursor, wd_end, prev_title, None, None))

        # Identify fragments.
        day_fragments: list[Fragment] = []
        for gap_start, gap_end, before, after, _next_id in gap_list:
            duration = int((gap_end - gap_start).total_seconds() / 60)
            if duration <= 0:
                continue
            if duration < min_useful_block_min:
                day_fragments.append(Fragment(
                    day=day,
                    start=gap_start,
                    end=gap_end,
                    duration_min=duration,
                    before_event=before,
                    after_event=after,
                ))
        fragments.extend(day_fragments)

        # Pair adjacent fragments that share a middle event.
        # If frag A and frag B both bracket a meeting, moving that meeting
        # would yield A.duration + meeting.duration + B.duration of contiguous
        # focus time.
        for i in range(len(day_fragments) - 1):
            frag_a = day_fragments[i]
            frag_b = day_fragments[i + 1]
            # The "middle" meeting is whichever event sits between A.end and B.start.
            middle: tuple[_dt.datetime, _dt.datetime, str, str | None] | None = None
            for sd, fd, title, ev_id in evs:
                if sd >= frag_a.end and fd <= frag_b.start:
                    middle = (sd, fd, title, ev_id)
                    break
            if not middle:
                continue
            if_moved = (
                frag_a.duration_min
                + int((middle[1] - middle[0]).total_seconds() / 60)
                + frag_b.duration_min
            )
            if if_moved >= min_useful_block_min:
                suggestions.append(DefragSuggestion(
                    day=day,
                    fragment_a=frag_a,
                    fragment_b=frag_b,
                    middle_event=middle[2],
                    middle_event_id=middle[3],
                    if_moved_block_min=if_moved,
                ))

    return DefragReport(
        fragments=fragments,
        suggestions=suggestions,
        days_analyzed=days_analyzed,
    )
