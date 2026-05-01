# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for reply-all guard against realistic real-world prose.

Run from project root:  python3 scripts/smoke_reply_all_guard.py

Each scenario is hand-written to match a pattern Finnn would actually
encounter. Expected verdicts are asserted at the bottom.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running from project root or scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reply_all_guard as core


SCENARIOS = [
    # --- 1. Classic reply-all-by-mistake: "Hi <name>, thanks!" ---------- #
    dict(
        name="Hi Sarah thanks (block)",
        body="Hi Sarah, thanks!",
        to=["sarah.fields@example.com", "brian@xenture.com", "conor@example.com",
            "amanda.miller@staffwizard.com"],
        cc=[],
        expect="block",
        expect_codes={"single_target_greeting", "ack_only_body"},
    ),

    # --- 2. Real substantive reply addressed to one person ------------- #
    dict(
        name="Hi Brian, real question (warn)",
        body=(
            "Hey Brian, can you double-check the totals on the Acme invoice "
            "before I send it through to AP? The amount looks a little off "
            "from what we discussed last week."
        ),
        to=["brian@xenture.com", "sarah@example.com", "conor@example.com"],
        cc=[],
        expect="warn",
        expect_codes={"single_target_greeting"},
    ),

    # --- 3. FYI to a small group — appropriate use of group --------- #
    dict(
        name="FYI to team (warn — info-only signal)",
        body=(
            "FYI — the contract with Anthropic is fully signed. "
            "Nothing required from anyone, just wanted to close the loop."
        ),
        to=["sarah@example.com", "brian@xenture.com", "amanda.miller@staffwizard.com"],
        cc=[],
        expect="warn",
        expect_codes={"fyi_body"},
    ),

    # --- 4. Substantive group ask — should be safe --------------------- #
    dict(
        name="Real group ask (safe)",
        body=(
            "Team — I'm pulling together the Q3 review deck. Each of you owns "
            "one section: Sarah on revenue, Brian on platform, Amanda on "
            "ops. Can you each send me 5 bullets by EOD Thursday?"
        ),
        to=["sarah@example.com", "brian@xenture.com", "amanda.miller@staffwizard.com"],
        cc=[],
        expect="safe",
        expect_codes=set(),
    ),

    # --- 5. CC-fanout with substantive body to one person ------------- #
    dict(
        name="One-to-one with CC fanout (warn — cc_fanout)",
        body=(
            "Hey Allan, looping you in directly on this. The renewal terms "
            "we discussed last quarter still hold — let's get the paperwork "
            "moving. Can you send the standard MSA this week?"
        ),
        to=["allan@example.com"],
        cc=["sarah@example.com", "brian@xenture.com",
            "amanda.miller@staffwizard.com", "conor@example.com"],
        expect="warn",
        expect_codes={"single_target_greeting", "cc_fanout"},
    ),

    # --- 6. Two-people greeting — single_target should NOT fire ------ #
    # Both names matched, no other signals → safe is the correct verdict.
    dict(
        name="Hi Sarah and Brian (safe)",
        body=(
            "Hi Sarah and Brian, quick check — are we still on for the "
            "Friday review? Want to make sure the deck lands on time."
        ),
        to=["sarah@example.com", "brian@xenture.com", "amanda.miller@staffwizard.com"],
        cc=[],
        expect="safe",
        expect_codes=set(),
    ),

    # --- 7. Ack with multiple recipients but no greeting -------------- #
    dict(
        name="Bare 'sounds good!' to group (warn)",
        body="sounds good!",
        to=["sarah@example.com", "brian@xenture.com", "amanda@example.com"],
        cc=[],
        expect="warn",
        expect_codes={"ack_only_body"},
    ),

    # --- 8. Self in recipients — sender exclusion ---------------------- #
    dict(
        name="Sender on own recipient list (safe)",
        body="Hi Sarah, can you confirm the timeline?",
        to=["sarah@example.com", "finn@surefox.com"],
        cc=[],
        sender="finn@surefox.com",
        expect="safe",  # only sarah is a real recipient, single = safe
        expect_codes=set(),
    ),

    # --- 9. Long substantive body greeting nobody — safe -------------- #
    dict(
        name="Strategy memo to many (safe)",
        body=(
            "Here's where I think we are after this week. Three things "
            "stand out: the new pipeline shape suggests we're going to "
            "land Q3 closer to plan than we feared, the platform "
            "investment is starting to pay off in support load, and "
            "we have a real opening with the StaffWizard side that we "
            "should plan for in the next quarter. I'd love each of "
            "your reactions before our Monday standup."
        ),
        to=["sarah@example.com", "brian@xenture.com",
            "amanda.miller@staffwizard.com", "conor@example.com"],
        cc=[],
        expect="safe",
        expect_codes=set(),
    ),
]


def run_scenario(s: dict) -> tuple[bool, str]:
    """Returns (pass, summary_line)."""
    v = core.score_draft(
        body=s["body"],
        to=s["to"],
        cc=s.get("cc", []),
        sender=s.get("sender"),
    )
    actual_codes = {sig.code for sig in v.signals if sig.code != "single_recipient"}
    expect_codes = s["expect_codes"]
    expect_verdict = s["expect"]

    verdict_ok = v.verdict == expect_verdict
    codes_ok = expect_codes.issubset(actual_codes) or expect_codes == actual_codes
    passed = verdict_ok and codes_ok

    status = "✓" if passed else "✗"
    addr = f" → {v.addressed_recipient}" if v.addressed_recipient else ""
    line = (
        f"{status} {s['name']:<55} "
        f"verdict={v.verdict:<5} (expected {expect_verdict:<5})  "
        f"signals={sorted(actual_codes)}{addr}"
    )
    return passed, line


def main() -> int:
    print("=" * 95)
    print("SMOKE TEST: reply-all guard against realistic prose")
    print("=" * 95)
    fails = 0
    for s in SCENARIOS:
        ok, line = run_scenario(s)
        print(line)
        if not ok:
            fails += 1
            # Print full verdict for debugging
            v = core.score_draft(
                body=s["body"], to=s["to"], cc=s.get("cc", []),
                sender=s.get("sender"),
            )
            print("    detail:")
            print("    " + json.dumps(v.to_dict(), indent=2).replace("\n", "\n    "))

    print("=" * 95)
    if fails:
        print(f"FAIL — {fails}/{len(SCENARIOS)} scenarios failed")
        return 1
    print(f"PASS — {len(SCENARIOS)}/{len(SCENARIOS)} scenarios match expected verdict")
    return 0


if __name__ == "__main__":
    sys.exit(main())
