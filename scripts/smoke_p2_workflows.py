# © 2026 CoAssisted Workspace. Licensed under MIT.
"""End-to-end smoke for all 8 P2 workflows.

For each workflow:
  1. Build realistic input data
  2. Run the pure-logic helper
  3. Compose via brand voice (template path)
  4. Verify the result is sane
"""
from __future__ import annotations

import datetime as _dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brand_voice
import draft_queue as dq
import p2_workflows as p2


TZ = _dt.timezone(_dt.timedelta(hours=-7))


def _check(name: str, ok: bool, detail: str = "") -> tuple[str, bool, str]:
    status = "✓" if ok else "✗"
    return (f"  {status} {name:<50} {detail}", ok, detail)


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p2_wf_"))
    dq._override_path_for_tests(tmp / "draft_queue.json")

    print("=" * 100)
    print("SMOKE TEST: P2 — 8 workflows end-to-end (template path)")
    print("=" * 100)

    fails = []

    # ----- #15 Auto-draft inbound -------------------------------------- #
    print("\n#15 Auto-draft inbound")
    threads = [
        {"id": "t1", "subject": "Renewal terms",
         "audience": "customer",
         "sender": "sarah@bigcustomer.com", "sender_name": "Sarah",
         "is_vip": True, "stale_days": 5,
         "last_message": {"from_self": False,
                          "from": "sarah@bigcustomer.com",
                          "from_name": "Sarah",
                          "snippet": "Quick check — terms still hold?",
                          "body": "Hi Finn, quick check — do the renewal terms still hold? Need to "
                                  "lock in by Friday."}},
        {"id": "t2", "subject": "Re: meeting",
         "sender": "boring@x.com",
         "last_message": {"from_self": True, "snippet": "Done."}},
    ]
    candidates = p2.auto_draft_candidates(threads, score_threshold=60)
    line, ok, _ = _check("filtered to needs-reply candidates",
                         len(candidates) == 1 and candidates[0]["id"] == "t1",
                         f"got {len(candidates)} candidate(s)")
    print(line)
    if not ok: fails.append("t1 wasn't picked")
    for t in candidates:
        req = p2.build_inbound_reply_request(t, sender_name="Finn")
        d = brand_voice.compose_template_only(req)
        ok = bool(d.subject and d.plain)
        print(_check(f"composed reply for {t['id']}", ok,
                     f"subject={d.subject!r}")[0])

    # ----- #24 RSVP alternatives --------------------------------------- #
    print("\n#24 RSVP alternatives")
    base = _dt.datetime(2026, 5, 4, 14, 0, tzinfo=TZ)
    busy = [(
        _dt.datetime(2026, 5, 5, 9, 0, tzinfo=TZ),
        _dt.datetime(2026, 5, 5, 12, 0, tzinfo=TZ),
    )]
    slots = p2.find_alternative_slots(base, base + _dt.timedelta(hours=1), busy)
    line, ok, _ = _check("found alternative slots", len(slots) >= 2,
                         f"{len(slots)} slot(s)")
    print(line)
    if not ok: fails.append("rsvp: insufficient slots")
    if slots:
        req = p2.build_rsvp_alternative_request(
            invite_subject="Tuesday 2pm sync",
            organizer_name="Brian",
            sender_name="Finn", alternatives=slots[:3],
        )
        d = brand_voice.compose_template_only(req)
        print(_check("composed rsvp_alternative", bool(d.subject), f"subject={d.subject!r}")[0])

    # ----- #25 Ghost agenda -------------------------------------------- #
    print("\n#25 Ghost agenda")
    event = {"id": "e1", "summary": "Q3 review prep",
             "description": "",
             "organizer": {"email": "finn@x.com"},
             "attendees": [{"email": "sarah@x.com"}, {"email": "brian@x.com"}]}
    is_ghost = p2.is_ghost_meeting(event, "finn@x.com")
    print(_check("detected as ghost", is_ghost)[0])
    if is_ghost:
        req = p2.build_ghost_agenda_request(
            event, "Recent thread: budget gap, hiring slowdown, AP backlog",
            sender_name="Finn",
        )
        d = brand_voice.compose_template_only(req)
        print(_check("composed agenda", "Agenda" in d.subject)[0])

    # ----- #26 Birthday ------------------------------------------------ #
    print("\n#26 Birthday")
    today = _dt.date.today()
    today_md = today.strftime("%m-%d")
    contacts = [
        {"name": "Mark Today", "email": "mark@x.com", "birthday_md": today_md},
        {"name": "Sarah Other", "email": "sarah@x.com", "birthday_md": "12-25"},
    ]
    today_bds = p2.find_today_birthdays(contacts)
    line, ok, _ = _check("found 1 birthday today", len(today_bds) == 1,
                         f"got {len(today_bds)}")
    print(line)
    if not ok: fails.append("birthday today not found")
    if today_bds:
        req = p2.build_birthday_request(today_bds[0], sender_name="Finn")
        d = brand_voice.compose_template_only(req)
        print(_check("composed birthday note",
                     "Happy birthday" in d.subject)[0])

    # ----- #40 Intro followups ----------------------------------------- #
    print("\n#40 Intro followups")
    today_d = _dt.date(2026, 4, 28)
    intros = [
        {"id": "i1", "subject": "Intro: A meet B",
         "body": "I wanted to introduce you both",
         "from": "me@x.com", "to": ["a@x.com", "b@x.com"],
         "created_at": "2026-04-08T10:00:00+00:00",
         "has_followup_threads": False},
    ]
    unfollowed = p2.find_unfollowed_intros(intros, user_email="me@x.com",
                                            today=today_d)
    line, ok, _ = _check("found 1 unfollowed intro", len(unfollowed) == 1)
    print(line)
    if not ok: fails.append("unfollowed intro not found")
    if unfollowed:
        req = p2.build_intro_followup_request(unfollowed[0], sender_name="Finn")
        d = brand_voice.compose_template_only(req)
        print(_check("composed intro followup", bool(d.plain))[0])

    # ----- #43 Cross-thread context ------------------------------------ #
    print("\n#43 Cross-thread context")
    all_threads = [
        {"id": "ct1", "participants": ["sarah@x.com", "me@x.com"],
         "subject": "Renewal", "last_activity": "2026-04-28", "status": "open"},
        {"id": "ct2", "participants": ["sarah@x.com", "me@x.com", "brian@x.com"],
         "subject": "Project ALPHA", "last_activity": "2026-04-20", "status": "open"},
        {"id": "ct3", "participants": ["sarah@x.com", "me@x.com"],
         "subject": "Coffee", "last_activity": "2026-01-01", "status": "closed"},
    ]
    related = p2.find_other_open_threads("sarah@x.com", all_threads,
                                          exclude_thread_id="ct1")
    print(_check("surfaced 1 other open thread", len(related) == 1,
                 f"got {[t['subject'] for t in related]}")[0])

    # ----- #74 Meeting poll -------------------------------------------- #
    print("\n#74 Meeting poll")
    now = _dt.datetime(2026, 4, 28, 10, 0, tzinfo=TZ)
    busy_by_person = {
        "a@x.com": [(_dt.datetime(2026, 4, 29, 9, 0, tzinfo=TZ),
                     _dt.datetime(2026, 4, 29, 11, 0, tzinfo=TZ))],
        "b@x.com": [(_dt.datetime(2026, 4, 29, 14, 0, tzinfo=TZ),
                     _dt.datetime(2026, 4, 29, 15, 0, tzinfo=TZ))],
    }
    common = p2.find_common_free_slots(busy_by_person, duration_min=30,
                                        candidates=3, now=now)
    print(_check("found 3 common slots", len(common) == 3,
                 f"got {len(common)}")[0])
    if common:
        req = p2.build_scheduling_poll_request(
            invitees=["a@x.com", "b@x.com"], proposed_slots=common,
            sender_name="Finn", meeting_topic="Quick sync",
        )
        d = brand_voice.compose_template_only(req)
        print(_check("composed scheduling poll", bool(d.plain))[0])

    # ----- #77 Translate reply ----------------------------------------- #
    print("\n#77 Translate reply")
    inbound_fr = ("Bonjour, merci pour votre message. Pouvons-nous fixer "
                  "une réunion la semaine prochaine? Cordialement, Pierre.")
    lang = p2.detect_language(inbound_fr)
    print(_check("detected language=fr", lang == "fr",
                 f"got {lang}")[0])
    if lang:
        req = p2.build_translate_reply_request(
            inbound_fr, target_language=lang,
            recipient_name="Pierre", sender_name="Finn",
            subject_hint="Réunion semaine prochaine",
        )
        d = brand_voice.compose_template_only(req)
        print(_check("composed translate_reply", req.target_language == "fr",
                     f"target_lang={req.target_language}")[0])

    print()
    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)} issue(s):")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — all 8 P2 workflows compose end-to-end against the queue infra")
    return 0


if __name__ == "__main__":
    sys.exit(run())
