# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for schedule defrag — realistic week of meetings."""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schedule_defrag as core


TZ = _dt.timezone(_dt.timedelta(hours=-7))


def evt(day_offset: int, sh: int, sm: int, eh: int, em: int, name: str) -> dict:
    base = _dt.date(2026, 4, 28) + _dt.timedelta(days=day_offset)
    s = _dt.datetime(base.year, base.month, base.day, sh, sm, tzinfo=TZ)
    e = _dt.datetime(base.year, base.month, base.day, eh, em, tzinfo=TZ)
    return {"id": f"{name.lower().replace(' ', '_')}_{day_offset}",
            "summary": name,
            "start": {"dateTime": s.isoformat()},
            "end": {"dateTime": e.isoformat()}}


def main() -> int:
    # Realistic Tuesday: meetings packed into 4 chunks with gaps.
    # 9-10  Standup (long block before)
    # 10:30-11 Quick sync       <- 30-min fragment after standup
    # 11:30-12:30 1:1 with Alex <- 30-min fragment after sync
    # 1:30-2:30 Customer review <- 60-min lunch (useful block, not fragment)
    # 3:00-3:20 Quick interview <- 30-min fragment after customer
    # 4:30-5:00 Wrap            <- 70-min block (above default threshold) — no fragment
    tuesday = [
        evt(0, 9, 0, 10, 0, "Standup"),
        evt(0, 10, 30, 11, 0, "Quick sync"),
        evt(0, 11, 30, 12, 30, "1:1 with Alex"),
        evt(0, 13, 30, 14, 30, "Customer review"),
        evt(0, 15, 0, 15, 20, "Interview"),
        evt(0, 16, 30, 17, 0, "Wrap"),
    ]

    # Wednesday: clean block-style day (no fragments)
    wednesday = [
        evt(1, 9, 0, 11, 0, "Strategy block"),
        evt(1, 13, 0, 15, 0, "Deep work"),
    ]

    events = tuesday + wednesday

    print("=" * 100)
    print("SMOKE TEST: schedule defrag — realistic 2-day window")
    print("=" * 100)

    report = core.find_fragments(events)

    print(f"\nDays analyzed: {report.days_analyzed}")
    print(f"Fragments: {len(report.fragments)}")
    for f in report.fragments:
        print(f"  - {f.day} {f.start.strftime('%H:%M')}-{f.end.strftime('%H:%M')} "
              f"({f.duration_min}min) "
              f"between '{f.before_event}' and '{f.after_event}'")

    print(f"\nDefrag suggestions: {len(report.suggestions)}")
    for s in report.suggestions:
        print(f"  - {s.day}: move '{s.middle_event}' → "
              f"unlocks {s.if_moved_block_min}min contiguous block")

    fails = []
    # Tuesday should have at least 3 fragments (post-standup, post-sync, post-interview)
    tuesday_frags = [f for f in report.fragments if f.day == "2026-04-28"]
    if len(tuesday_frags) < 2:
        fails.append(f"Tuesday should have ≥2 fragments, got {len(tuesday_frags)}")
    # Wednesday should have zero fragments (clean block-style day)
    wed_frags = [f for f in report.fragments if f.day == "2026-04-29"]
    if wed_frags:
        fails.append(f"Wednesday should be fragment-free, got {len(wed_frags)}")
    # Should have at least one defrag suggestion (Quick sync between standup and 1:1)
    if not report.suggestions:
        fails.append("Expected at least one defrag suggestion")

    print()
    print("=" * 100)
    if fails:
        print("FAIL")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — fragments + suggestions match expected pattern")

    print()
    print("Sample full report:")
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
