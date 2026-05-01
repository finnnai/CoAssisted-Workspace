# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for access audit against realistic permission lists.

Run from project root:  python3 scripts/smoke_access_audit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import access_audit as core
import sender_classifier


# Lock the user-domain so internal/external classification is deterministic
# regardless of whether OAuth has been refreshed in this environment.
sender_classifier._override_for_tests(auto_domain="surefox.com")


SCENARIOS = [
    # 1. Tight internal-only folder — should score 0.
    dict(
        name="Internal-only project folder",
        file_id="folder_alpha", file_name="Project ALPHA",
        permissions=[
            {"id": "p1", "type": "user", "role": "owner",
             "emailAddress": "user1@domain.com",
             "displayName": "User 1"},
            {"id": "p2", "type": "user", "role": "writer",
             "emailAddress": "user2@domain.com",
             "displayName": "User 2"},
            {"id": "p3", "type": "user", "role": "writer",
             "emailAddress": "user3@domain.com",
             "displayName": "User 3"},
        ],
        expect_score=0,
        expect_flags=set(),
    ),

    # 2. Subsidiary access — should classify subsidiary, no flags.
    dict(
        name="Subsidiary collaborator",
        file_id="folder_beta", file_name="Project BETA",
        permissions=[
            {"id": "p1", "type": "user", "role": "owner",
             "emailAddress": "finn@surefox.com"},
            {"id": "p2", "type": "user", "role": "writer",
             "emailAddress": "amanda.miller@staffwizard.com",
             "displayName": "Amanda Miller"},
            {"id": "p3", "type": "user", "role": "writer",
             "emailAddress": "brian@xenture.com",
             "displayName": "Brian Sweigart"},
        ],
        expect_score=0,
        expect_flags=set(),
    ),

    # 3. External vendor with reader access — low risk.
    dict(
        name="External vendor reader",
        file_id="folder_gamma", file_name="Vendor MSA",
        permissions=[
            {"id": "p1", "type": "user", "role": "owner",
             "emailAddress": "finn@surefox.com"},
            {"id": "p2", "type": "user", "role": "reader",
             "emailAddress": "vendor@anthropic.com",
             "displayName": "Vendor Contact"},
        ],
        expect_score=0,  # external reader is fine — not flagged
        expect_flags=set(),
    ),

    # 4. External vendor with WRITER access — should flag.
    dict(
        name="External writer (concerning)",
        file_id="folder_delta", file_name="Sensitive folder",
        permissions=[
            {"id": "p1", "type": "user", "role": "owner",
             "emailAddress": "finn@surefox.com"},
            {"id": "p2", "type": "user", "role": "writer",
             "emailAddress": "freelancer@gmail.com"},
        ],
        expect_score=25,
        expect_flags={"external_writer"},
    ),

    # 5. Anyone-with-link (writer) — high alarm.
    dict(
        name="Public writable link (red alert)",
        file_id="folder_eps", file_name="Quarterly report",
        permissions=[
            {"id": "p1", "type": "user", "role": "owner",
             "emailAddress": "finn@surefox.com"},
            {"id": "p2", "type": "anyone", "role": "writer"},
        ],
        expect_score=70,  # 50 (public_writable) + 20 (anyone_with_link)
        expect_flags={"anyone_with_link", "public_writable"},
    ),

    # 6. Mixed — leftover grants from former vendor + new exec collaborator.
    dict(
        name="Mixed: deleted account + ext writer + clean internal",
        file_id="folder_zeta", file_name="Annual planning",
        permissions=[
            {"id": "p1", "type": "user", "role": "owner",
             "emailAddress": "finn@surefox.com"},
            {"id": "p2", "type": "user", "role": "reader",
             "emailAddress": "ghost@oldvendor.com",
             "displayName": "Old Vendor", "deleted": True},
            {"id": "p3", "type": "user", "role": "writer",
             "emailAddress": "consultant@external.io"},
            {"id": "p4", "type": "user", "role": "reader",
             "emailAddress": "alex@surefox.com"},
        ],
        expect_score=30,  # 5 (deleted) + 25 (external_writer)
        expect_flags={"deleted_account", "external_writer"},
    ),
]


def run_scenario(s: dict) -> tuple[bool, str, dict]:
    report = core.summarize_permissions(
        file_id=s["file_id"], file_name=s["file_name"],
        permissions=s["permissions"],
        authed_email="finn@surefox.com",
    )
    actual_flags = set(report.summary["risk_flags"].keys())
    score_ok = report.risk_score == s["expect_score"]
    flags_ok = actual_flags == s["expect_flags"]
    passed = score_ok and flags_ok

    status = "✓" if passed else "✗"
    line = (
        f"{status} {s['name']:<55} "
        f"score={report.risk_score:>3} (expected {s['expect_score']:>3})  "
        f"flags={sorted(actual_flags)}"
    )
    return passed, line, report.to_dict()


def main() -> int:
    print("=" * 100)
    print("SMOKE TEST: access audit against realistic permission scenarios")
    print("=" * 100)
    fails = 0
    for s in SCENARIOS:
        ok, line, detail = run_scenario(s)
        print(line)
        if not ok:
            fails += 1
            print("    full report:")
            print("    " + json.dumps(detail, indent=2).replace("\n", "\n    "))
    print("=" * 100)
    if fails:
        print(f"FAIL — {fails}/{len(SCENARIOS)} scenarios failed")
        return 1
    print(f"PASS — {len(SCENARIOS)}/{len(SCENARIOS)} scenarios match expected score + flags")

    # Bonus: walk the full report on the mixed scenario for visual inspection.
    print()
    print("=" * 100)
    print("Sample full report (mixed-risk folder):")
    print("=" * 100)
    mixed = next(s for s in SCENARIOS if "Mixed" in s["name"])
    report = core.summarize_permissions(
        file_id=mixed["file_id"], file_name=mixed["file_name"],
        permissions=mixed["permissions"],
        authed_email="finn@surefox.com",
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
