# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the Executive Briefing core (composer + HTML rendering + actions)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import briefing_actions
import executive_briefing as core
import external_feeds as ef
import weather as _weather


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    briefing_actions._override_path_for_tests(tmp_path / "ba.json")
    ef._override_cache_path_for_tests(tmp_path / "ef.json")
    ef.unfreeze()
    yield
    ef.unfreeze()
    ef._override_cache_path_for_tests(
        Path(__file__).resolve().parent.parent / "external_feeds_cache.json",
    )
    briefing_actions._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "briefing_actions.json",
    )


# --------------------------------------------------------------------------- #
# Briefing actions store
# --------------------------------------------------------------------------- #


def test_enqueue_returns_token_and_persists():
    token = briefing_actions.enqueue("approve_send", {"draft_id": "d1"})
    assert len(token) >= 6
    rec = briefing_actions.get(token)
    assert rec is not None
    assert rec["status"] == "pending"


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        briefing_actions.enqueue("bogus_kind", {})


def test_mark_executed_changes_status():
    token = briefing_actions.enqueue("mark_read", {"thread_id": "t1"})
    briefing_actions.mark_executed(token, {"ok": True})
    assert briefing_actions.get(token)["status"] == "executed"


def test_re_execute_no_op():
    token = briefing_actions.enqueue("mark_read", {"thread_id": "t1"})
    briefing_actions.mark_executed(token, {"ok": True})
    assert briefing_actions.mark_executed(token, {"ok": False}) is None


def test_expire_old_marks_expired_pending():
    token = briefing_actions.enqueue("mark_read", {"thread_id": "t1"}, ttl_hours=0)
    # Manually backdate
    data = briefing_actions._load()
    data[token]["expires_at"] = (
        _dt.datetime.now().astimezone() - _dt.timedelta(hours=1)
    ).isoformat()
    briefing_actions._save(data)
    n = briefing_actions.expire_old()
    assert n == 1
    assert briefing_actions.get(token)["status"] == "expired"


# --------------------------------------------------------------------------- #
# Compose briefing
# --------------------------------------------------------------------------- #


def _email_item(idx: int = 1) -> core.EmailItem:
    return core.EmailItem(
        thread_id=f"t{idx}",
        sender_name=f"Sender {idx}",
        sender_email=f"s{idx}@example.com",
        subject=f"Subject {idx}",
        snippet=f"Snippet text {idx}",
        drafted_reply=f"Hi, here is my drafted reply #{idx}.",
    )


def _meeting_item(idx: int = 1) -> core.MeetingItem:
    return core.MeetingItem(
        event_id=f"e{idx}",
        summary=f"Meeting {idx}",
        start_iso="2026-04-29T10:00:00-07:00",
        end_iso="2026-04-29T10:30:00-07:00",
        start_label="10:00 AM",
        location="Office",
        attendee_count=3,
        is_organizer=False,
    )


def _task_item(idx: int = 1) -> core.TaskItem:
    return core.TaskItem(
        task_id=f"task{idx}",
        tasklist_id="default",
        title=f"Task {idx}",
        notes="some notes",
        due_iso="2026-04-29",
    )


def test_compose_attaches_actions_to_each_item():
    brief = core.compose_briefing(
        date="2026-04-29",
        greeting_name="Finn",
        user_email="finn@x.com",
        weather_forecast=None,
        email_items=[_email_item(1)],
        meeting_items=[_meeting_item(1)],
        task_items=[_task_item(1)],
    )
    assert len(brief.emails[0].actions) == 4
    assert len(brief.meetings[0].actions) == 3
    assert len(brief.tasks[0].actions) == 3


def test_email_actions_have_known_kinds():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[_email_item(1)], meeting_items=[], task_items=[],
    )
    kinds = [a.kind for a in brief.emails[0].actions]
    assert kinds == ["approve_send", "schedule_send", "mark_read", "mark_as_task"]


def test_meeting_actions_have_known_kinds():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[], meeting_items=[_meeting_item(1)], task_items=[],
    )
    kinds = [a.kind for a in brief.meetings[0].actions]
    assert kinds == ["accept_meeting", "decline_meeting", "suggest_new_time"]


def test_task_actions_have_known_kinds():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[], meeting_items=[], task_items=[_task_item(1)],
    )
    kinds = [a.kind for a in brief.tasks[0].actions]
    assert kinds == ["complete_task", "ignore_task", "schedule_to_calendar"]


def test_each_action_token_persists_in_store():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[_email_item(1)],
        meeting_items=[_meeting_item(1)],
        task_items=[_task_item(1)],
    )
    for sec in (brief.emails, brief.meetings, brief.tasks):
        for item in sec:
            for action in item.actions:
                assert briefing_actions.get(action.token) is not None


def test_summary_line_counts():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[_email_item(1), _email_item(2)],
        meeting_items=[_meeting_item(1), _meeting_item(2), _meeting_item(3)],
        task_items=[_task_item(1)],
    )
    s = brief.summary_line()
    assert "2 emails" in s
    assert "3 meetings" in s
    assert "1 active tasks" in s


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #


def test_render_html_includes_greeting_and_summary():
    brief = core.compose_briefing(
        date="2026-04-29", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[_email_item(1)], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    # Greeting picks Morning/Afternoon/Evening based on the local clock.
    assert any(f"Good {w}, Finn" in html for w in ("Morning", "Afternoon", "Evening"))
    assert "EXECUTIVE BRIEF" in html
    assert "EMAIL TRIAGE" in html
    # Real tabs — radio inputs + label markup must be present
    assert 'name="standup_tab"' in html
    assert 'for="tab-emails"' in html
    assert 'for="tab-meetings"' in html
    assert 'for="tab-tasks"' in html


def test_render_html_with_weather_strip():
    forecast = _weather.DailyForecast(
        location_label="San Francisco, CA",
        fetched_at="2026-04-29T06:00:00-07:00",
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=68, low_f=54, summary="Mostly clear",
        hourly=[
            _weather.HourlyForecast(
                hour_local="06:00", temp_f=58, feels_like_f=56,
                condition="clear", icon="☀️",
                description="Sunny", precip_chance_pct=0, wind_mph=5,
            ),
            _weather.HourlyForecast(
                hour_local="12:00", temp_f=66, feels_like_f=64,
                condition="partly_cloudy", icon="🌤️",
                description="Partly cloudy", precip_chance_pct=10, wind_mph=8,
            ),
        ],
    )
    brief = core.compose_briefing(
        date="2026-04-29", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=forecast,
        email_items=[], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    assert "WEATHER" in html
    assert "San Francisco" in html
    # Outline SVG icons (paths/circles), not raw emoji
    assert "stroke-linecap" in html


def test_render_html_includes_action_buttons():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[_email_item(1)], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    # Compact icon-forward labels
    assert "↑ Send" in html
    assert "Read" in html
    assert "Task" in html
    # Multiple actions render — submit button + at least 3 anchor links
    assert "<button type=\"submit\"" in html
    assert html.count("<a href") >= 3


def test_render_html_escapes_user_content():
    item = core.EmailItem(
        thread_id="t1", sender_name="<script>alert(1)</script>",
        sender_email="x@y.com", subject="<b>html</b> in subject",
        snippet="snippet", drafted_reply="reply",
    )
    brief = core.compose_briefing(
        date="x", greeting_name="<Finn>", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[item], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;Finn&gt;" in html


def test_render_html_no_items_still_renders():
    brief = core.compose_briefing(
        date="x", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=None,
        email_items=[], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    assert any(f"Good {w}" in html for w in ("Morning", "Afternoon", "Evening"))
    # No section headers when empty
    assert "TAB 1" not in html


# --------------------------------------------------------------------------- #
# Weather significant changes
# --------------------------------------------------------------------------- #


def test_weather_detects_clear_to_rain_transition():
    hourly = [
        _weather.HourlyForecast(
            hour_local=f"{h:02d}:00", temp_f=60, feels_like_f=58,
            condition=cond, icon=_weather._icon_for(cond),
            description=cond, precip_chance_pct=0, wind_mph=5,
        )
        for h, cond in [(9, "clear"), (12, "clear"), (15, "rain"), (18, "clear")]
    ]
    out = _weather.detect_significant_changes(hourly)
    # Should mark transitions at index 2 (clear→rain) and 3 (rain→clear)
    assert 2 in out
    assert 3 in out


def test_weather_no_changes_returns_empty():
    hourly = [
        _weather.HourlyForecast(
            hour_local=f"{h:02d}:00", temp_f=60, feels_like_f=58,
            condition="clear", icon="☀️", description="Sunny",
            precip_chance_pct=0, wind_mph=5,
        )
        for h in range(6, 22, 3)
    ]
    out = _weather.detect_significant_changes(hourly)
    assert out == []


# --------------------------------------------------------------------------- #
# Weather adapter
# --------------------------------------------------------------------------- #


def test_weather_fixture_fallback_when_frozen_dict():
    fixture = _weather._fixture_forecast("Anywhere, USA")
    _weather.freeze_for_tests("Anywhere, USA", fixture.to_dict())
    out = _weather.get_today_forecast("Anywhere, USA")
    assert out.location_label == "Anywhere, USA"
    assert len(out.hourly) > 0


# --------------------------------------------------------------------------- #
# JSON shape
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Outline icons + news rendering
# --------------------------------------------------------------------------- #


def test_render_uses_outline_svg_icons_not_emoji():
    """The rendered HTML should contain SVG paths (outline icons), not the
    raw emoji that the old version used in the strip."""
    forecast = _weather.DailyForecast(
        location_label="Test City", fetched_at="x",
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=70, low_f=55, summary="x",
        hourly=[
            _weather.HourlyForecast(
                hour_local="00:00", temp_f=55, feels_like_f=53,
                condition="clear_night", icon="🌙", description="Clear",
                precip_chance_pct=0, wind_mph=4,
            ),
        ],
    )
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=forecast,
        email_items=[], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    # Outline icons render as <g transform="translate(...)" ...><path .../></g>
    assert 'transform="translate(' in html
    assert 'stroke-linecap="round"' in html


def test_render_includes_ideal_zone_label():
    """Default 65-75 range when city isn't in the lookup table."""
    forecast = _weather.DailyForecast(
        location_label="Tinytown, ZZ", fetched_at="x",
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=72, low_f=60, summary="x",
        hourly=[
            _weather.HourlyForecast(
                hour_local="12:00", temp_f=68, feels_like_f=66,
                condition="clear", icon="☀️", description="Sunny",
                precip_chance_pct=0, wind_mph=4,
            ),
        ],
    )
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=forecast,
        email_items=[], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    assert "IDEAL ZONE 65" in html


def test_ideal_range_lookup_known_cities():
    assert _weather.get_ideal_range("San Francisco, CA") == (60, 72)
    assert _weather.get_ideal_range("Phoenix, AZ") == (75, 90)
    assert _weather.get_ideal_range("Miami, FL") == (75, 88)


def test_ideal_range_lookup_first_token_match():
    # "Boston" alone (no state) should still match
    assert _weather.get_ideal_range("Boston") == (62, 75)


def test_ideal_range_lookup_substring_fallback():
    # Address-like strings still find the city
    assert _weather.get_ideal_range("Downtown Seattle, WA") == (60, 72)


def test_ideal_range_default_for_unknown():
    assert _weather.get_ideal_range("Tinytown, ZZ") == (65, 75)
    assert _weather.get_ideal_range("") == (65, 75)


def test_render_uses_per_city_ideal_range():
    """SF should render 60-72, not the generic 65-75."""
    forecast = _weather.DailyForecast(
        location_label="San Francisco, CA", fetched_at="x",
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=68, low_f=54, summary="x",
        hourly=[
            _weather.HourlyForecast(
                hour_local="12:00", temp_f=64, feels_like_f=62,
                condition="clear", icon="☀️", description="Sunny",
                precip_chance_pct=0, wind_mph=4,
            ),
        ],
    )
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=forecast,
        email_items=[], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    assert "IDEAL ZONE 60" in html


def test_render_includes_dark_navy_header_and_footer_bands():
    """Dark navy header at top + thin navy footer at bottom of chart card."""
    forecast = _weather.DailyForecast(
        location_label="San Francisco, CA", fetched_at="x",
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=68, low_f=54, summary="x",
        hourly=[
            _weather.HourlyForecast(
                hour_local="12:00", temp_f=64, feels_like_f=62,
                condition="clear", icon="☀️", description="Sunny",
                precip_chance_pct=0, wind_mph=4,
            ),
        ],
    )
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=forecast,
        email_items=[], meeting_items=[], task_items=[],
    )
    html = core.render_email_html(brief)
    # Navy band shows up twice now (header + footer)
    assert html.count("#0d1f3a") >= 2
    # Eyebrow word should NOT appear (we removed it)
    assert ">MORNING<" not in html


def test_render_news_tab_with_items():
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=None,
        email_items=[], meeting_items=[], task_items=[],
        news_items=[
            {"title": "Tech rallies on earnings",
             "source": "Reuters",
             "url": "https://example.com/n1",
             "snippet": "Markets opened higher.",
             "thumb_url": None,
             "thumb_color": "#1a4f8c",
             "published_at": "2026-04-29T05:00:00-07:00"},
        ],
    )
    html = core.render_email_html(brief)
    # News is now its own tab — the panel header reads WORLD NEWS,
    # the tab label says "News", and item content renders inside the panel.
    assert 'id="panel-news"' in html
    assert "WORLD NEWS" in html
    assert "Tech rallies on earnings" in html
    assert "https://example.com/n1" in html


def test_greeting_word_buckets():
    # 5–11 morning, 12–17 afternoon, 18–4 evening
    assert core._greeting_word(_dt.datetime(2026, 4, 29,  6, 0)) == "morning"
    assert core._greeting_word(_dt.datetime(2026, 4, 29, 11, 59)) == "morning"
    assert core._greeting_word(_dt.datetime(2026, 4, 29, 12, 0)) == "afternoon"
    assert core._greeting_word(_dt.datetime(2026, 4, 29, 17, 59)) == "afternoon"
    assert core._greeting_word(_dt.datetime(2026, 4, 29, 18, 0)) == "evening"
    assert core._greeting_word(_dt.datetime(2026, 4, 29, 23, 30)) == "evening"
    assert core._greeting_word(_dt.datetime(2026, 4, 29,  2, 30)) == "evening"
    assert core._greeting_word(_dt.datetime(2026, 4, 29,  4, 59)) == "evening"
    assert core._greeting_word(_dt.datetime(2026, 4, 29,  5, 0)) == "morning"


def test_render_news_tab_empty_state():
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=None,
        email_items=[], meeting_items=[], task_items=[],
        news_items=[],
    )
    html = core.render_email_html(brief)
    # Tab still renders — but body shows the empty state placeholder.
    assert 'id="panel-news"' in html
    assert "No news right now." in html


def test_news_to_dict_carries_through():
    brief = core.compose_briefing(
        date="x", greeting_name="X", user_email="x@x.com",
        weather_forecast=None,
        email_items=[], meeting_items=[], task_items=[],
        news_items=[{"title": "T", "url": "u", "source": "s",
                     "snippet": "x", "thumb_url": None,
                     "thumb_color": "#000", "published_at": ""}],
    )
    d = brief.to_dict()
    assert "news" in d
    assert len(d["news"]) == 1


def test_to_dict_full_shape():
    brief = core.compose_briefing(
        date="2026-04-29", greeting_name="Finn", user_email="finn@x.com",
        weather_forecast=_weather._fixture_forecast("San Francisco, CA"),
        email_items=[_email_item(1)],
        meeting_items=[_meeting_item(1)],
        task_items=[_task_item(1)],
    )
    d = brief.to_dict()
    assert d["date"] == "2026-04-29"
    assert d["weather"] is not None
    assert d["weather"]["location_label"] == "San Francisco, CA"
    assert len(d["emails"]) == 1
    assert len(d["meetings"]) == 1
    assert len(d["tasks"]) == 1
    assert "summary" in d
    # Each action item should have a token
    for action in d["emails"][0]["actions"]:
        assert action["token"]
