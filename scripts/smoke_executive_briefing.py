# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for daily standup — generates a sample HTML + JSON
you can open in the browser to inspect the visual + actionable layout.

Run from project root:
    python3 scripts/smoke_executive_briefing.py

Outputs to /sessions/.../mnt/Desktop/ so you can preview in your browser.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import briefing_actions
import executive_briefing as core
import external_feeds as ef
import news_feed
import weather as _weather


# Output path — Desktop on macOS user's machine via Cowork mount
OUT_DIR = Path("/sessions/elegant-serene-volta/mnt/Desktop")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_ceo_"))
    briefing_actions._override_path_for_tests(tmp / "briefing_actions.json")
    ef._override_cache_path_for_tests(tmp / "ef.json")

    print("=" * 100)
    print("SMOKE TEST: Executive Briefing")
    print("=" * 100)

    # ---- Build a realistic synthetic dataset ------------------------- #

    # Weather: SF full-day forecast with afternoon rain.
    # 8 readings at exact 3-hour intervals so the chart has full 0000-2359 view.
    sf_weather = _weather.DailyForecast(
        location_label="San Francisco, CA",
        fetched_at="2026-04-29T05:30:00-07:00",
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=68, low_f=52,
        summary="Mostly clear with afternoon rain · High 68°F · Low 52°F",
        hourly=[
            _hr("00:00", 54, "clear_night"),
            _hr("03:00", 52, "clear_night"),
            _hr("06:00", 56, "clear"),
            _hr("09:00", 62, "clear"),
            _hr("12:00", 67, "partly_cloudy"),
            _hr("15:00", 63, "rain"),
            _hr("18:00", 59, "rain"),
            _hr("21:00", 56, "clear_night"),
        ],
    )

    # Email items — 3 inbound threads with drafted replies + sample attachments
    email_items = [
        core.EmailItem(
            thread_id="t1",
            sender_name="Sarah Fields",
            sender_email="sarah@bigcustomer.com",
            subject="Renewal terms — quick check",
            snippet=("Hey, wanted to circle back on the renewal terms we discussed "
                     "last week. Are we still good to lock in by Friday?"),
            drafted_reply=("Hi Sarah,\n\nYes, terms hold. I'll have the signed "
                           "MSA back to you by EOD Thursday so we lock in by Friday.\n\n"
                           "Thanks,\nFinn"),
            draft_id="DRAFT_FAKE_001",
            received_at="2026-04-29T04:42:00-07:00",
            attachments=[],
        ),
        core.EmailItem(
            thread_id="t2",
            sender_name="Allan Renazco",
            sender_email="allan@anothercustomer.com",
            subject="Contract redlines for review",
            snippet=("Attached are our redlines on the platform agreement. "
                     "Most are minor; the IP carve-out is the one that needs your eyes."),
            drafted_reply=("Hi Allan,\n\nGot the redlines — will get to them today. "
                           "I'll come back with a marked-up version + a 30-min slot to "
                           "discuss the IP carve-out.\n\nThanks,\nFinn"),
            draft_id="DRAFT_FAKE_002",
            received_at="2026-04-29T03:15:00-07:00",
            attachments=[
                core.EmailAttachment(
                    name="Platform_MSA_redlines_v3.pdf",
                    url="https://mail.google.com/mail/u/0/#inbox/t2?attid=001",
                    mime_type="application/pdf",
                    size_bytes=384_512,
                ),
                core.EmailAttachment(
                    name="IP_Carveout_diff.docx",
                    url="https://mail.google.com/mail/u/0/#inbox/t2?attid=002",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    size_bytes=42_300,
                ),
            ],
        ),
        core.EmailItem(
            thread_id="t3",
            sender_name="Brian Sweigart",
            sender_email="brian@xenture.com",
            subject="Q3 platform update",
            snippet=("Quick update on the platform team — we hit the migration "
                     "milestone, slightly behind on the API cleanup."),
            drafted_reply=("Hey Brian,\n\nThanks for the update. The migration win "
                           "is huge — let's celebrate it on Friday. On API cleanup, "
                           "what does the new ETA look like?\n\nFinn"),
            draft_id="DRAFT_FAKE_003",
            received_at="2026-04-29T01:22:00-07:00",
            attachments=[
                core.EmailAttachment(
                    name="Q3_metrics.xlsx",
                    url="https://mail.google.com/mail/u/0/#inbox/t3?attid=003",
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    size_bytes=128_000,
                ),
            ],
        ),
    ]

    # Meeting items — typical CEO day
    meeting_items = [
        core.MeetingItem(
            event_id="evt_111", summary="1:1 with Alex (CTO)",
            start_iso="2026-04-29T09:00:00-07:00",
            end_iso="2026-04-29T09:30:00-07:00",
            start_label="9:00 AM", location="Office HQ",
            attendee_count=2, is_organizer=True,
        ),
        core.MeetingItem(
            event_id="evt_222", summary="Customer demo: Anthropic",
            start_iso="2026-04-29T11:00:00-07:00",
            end_iso="2026-04-29T11:45:00-07:00",
            start_label="11:00 AM", location="Zoom",
            attendee_count=4, is_organizer=False,
        ),
        core.MeetingItem(
            event_id="evt_333", summary="Board prep — quarterly review",
            start_iso="2026-04-29T14:00:00-07:00",
            end_iso="2026-04-29T15:00:00-07:00",
            start_label="2:00 PM", location="Boardroom",
            attendee_count=6, is_organizer=True,
        ),
        core.MeetingItem(
            event_id="evt_444", summary="Team standup",
            start_iso="2026-04-29T16:00:00-07:00",
            end_iso="2026-04-29T16:15:00-07:00",
            start_label="4:00 PM", location="",
            attendee_count=8, is_organizer=False,
        ),
    ]

    # Task items
    task_items = [
        core.TaskItem(
            task_id="task_a", tasklist_id="default",
            title="Sign Acme MSA",
            notes="Hard copy on desk. Counter-signed by Acme yesterday.",
            due_iso="2026-04-29",
        ),
        core.TaskItem(
            task_id="task_b", tasklist_id="default",
            title="Review board deck v3",
            notes="Latest draft in Drive. Need to OK before Wed.",
            due_iso="2026-04-30",
        ),
        core.TaskItem(
            task_id="task_c", tasklist_id="default",
            title="Q3 strategy memo (3 sections)",
            notes="Pricing, platform, hiring.",
            due_iso="2026-05-02",
        ),
        core.TaskItem(
            task_id="task_d", tasklist_id="default",
            title="Reply to Mark Adams",
            notes="Stale 60d+. Old vendor relationship.",
            due_iso=None,
        ),
        core.TaskItem(
            task_id="task_e", tasklist_id="default",
            title="Prep travel to NYC May 15",
            notes="Per-diem + hotel + return-day recovery block.",
            due_iso="2026-05-10",
        ),
    ]

    # ---- News (uses fixture path since the cache is in tmp) ----------- #
    news_items = news_feed.get_top_news(limit=6)

    # ---- Compose + render --------------------------------------------- #
    brief = core.compose_briefing(
        date="2026-04-29",
        greeting_name="Finn",
        user_email="finnn@surefox.com",
        weather_forecast=sf_weather,
        email_items=email_items,
        meeting_items=meeting_items,
        task_items=task_items,
        news_items=news_items,
    )

    print(f"\nSummary: {brief.summary_line()}")
    sig_changes = brief.weather_significant_change_idx
    print(f"Weather significant change indices: {sig_changes} "
          f"(should highlight afternoon rain onset)")

    # Verify token store now has 4 emails×3 + 3 meetings×4 + 5 tasks×3 = 12+12+15 = wait
    # actually: 3 emails × 4 actions = 12, 4 meetings × 3 = 12, 5 tasks × 3 = 15. Total 39
    pending = briefing_actions.list_pending()
    print(f"Action tokens registered: {len(pending)}")
    expected_tokens = 3 * 4 + 4 * 3 + 5 * 3
    assert len(pending) == expected_tokens, f"expected {expected_tokens}, got {len(pending)}"
    print(f"  ✓ matches expected ({expected_tokens})")

    # ---- Write the HTML preview + JSON ------------------------------- #
    html_body = core.render_email_html(brief)
    json_payload = brief.to_dict()

    # Wrap the body in a minimal HTML shell so it opens correctly in browsers
    html_doc = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Executive Briefing — preview</title></head>"
        f"<body style='margin:0;padding:0;'>{html_body}</body></html>"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUT_DIR / "executive_briefing_preview.html"
    json_path = OUT_DIR / "executive_briefing_sample.json"
    html_path.write_text(html_doc, encoding="utf-8")
    json_path.write_text(json.dumps(json_payload, indent=2, default=str),
                         encoding="utf-8")

    print(f"\n✓ HTML preview written: {html_path}")
    print(f"  ({len(html_doc)} chars, {len(html_doc.splitlines())} lines)")
    print(f"✓ JSON sample written:  {json_path}")
    print(f"  ({len(json.dumps(json_payload))} chars)")

    print()
    print("=" * 100)
    print("PASS — open the HTML preview in your browser to inspect the visual.")
    return 0


def _hr(time, temp, condition):
    return _weather.HourlyForecast(
        hour_local=time, temp_f=temp, feels_like_f=temp - 2,
        condition=condition,
        icon=_weather._icon_for(condition),
        description=_weather._description_for(condition),
        precip_chance_pct=70 if condition == "rain" else 0,
        wind_mph=8,
    )


if __name__ == "__main__":
    sys.exit(main())
