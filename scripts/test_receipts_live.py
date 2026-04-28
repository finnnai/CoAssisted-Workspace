#!/usr/bin/env python3
# See LICENSE file for terms.
"""Live-API smoke test for the receipt extractor.

Runs ONE real Anthropic API call against a known-good Uber receipt fixture
and validates the output. Used to:

  - Confirm the prompt produces well-formed JSON
  - Measure token cost per receipt empirically
  - Spot regressions in extraction quality after prompt edits

Cost: ~$0.0005 per run. Safe to run repeatedly.

Run from Terminal (NOT from inside Cowork):

    cd "/Users/finnnai/Claude/google_workspace_mcp"
    .venv/bin/python scripts/test_receipts_live.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make project root importable when running this script from anywhere.
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))


# A representative Uber receipt body. Realistic format with all the parts
# we expect to extract: merchant, total, currency, line items, tax, card
# last 4, date, location, category.
SAMPLE_RECEIPT = """\
Thanks for riding with Uber

Your Friday afternoon trip with Uber

Total $24.18

Trip fare    $20.50
Booking fee  $2.10
Tax          $1.58

Charged to Visa **** 4392
Apr 26, 2026 · 4:32 PM

Trip to: 415 Mission St, San Francisco, CA 94105

Driver: Allan
Vehicle: Toyota Prius

If you didn't take this trip, please tell us:
https://help.uber.com
"""


def assert_eq(label, actual, expected, tolerate_close=False):
    """Pretty-print a single assertion, return True if it passed."""
    if tolerate_close and isinstance(expected, float) and isinstance(actual, (int, float)):
        ok = abs(float(actual) - expected) < 0.02
    else:
        ok = actual == expected
    icon = "✓" if ok else "✗"
    color = "\033[32m" if ok else "\033[31m"
    print(f"  {color}{icon}\033[0m {label}: expected={expected!r}, actual={actual!r}")
    return ok


def main() -> int:
    print("\033[1m" + "=" * 70 + "\033[0m")
    print("\033[1m  CoAssisted Workspace — Receipt Extractor Live Smoke Test\033[0m")
    print("\033[1m" + "=" * 70 + "\033[0m\n")

    # 1. Verify Anthropic key is configured
    try:
        import llm
        ok, reason = llm.is_available()
    except Exception as e:
        print(f"\033[31m✗ Could not import llm module: {e}\033[0m")
        return 1
    if not ok:
        print(f"\033[31m✗ Anthropic API not available: {reason}\033[0m")
        print("  Fix: add to config.json: { \"anthropic_api_key\": \"sk-ant-api03-...\" }")
        return 1
    print(f"\033[32m✓ Anthropic API configured\033[0m\n")

    # 2. Run extraction on the known sample
    import receipts
    print("Sending sample Uber receipt to Claude Haiku 4.5...")
    print(f"  Sample length: {len(SAMPLE_RECEIPT)} chars\n")

    import time
    t0 = time.time()
    rec = receipts.extract_from_text(
        SAMPLE_RECEIPT, source_id="live_smoke_test",
    )
    elapsed = time.time() - t0
    print(f"  Returned in {elapsed:.2f}s\n")

    # 3. Validate the output
    print("\033[1mExtracted fields:\033[0m")
    print(json.dumps(rec.model_dump(), indent=2))
    print()
    print("\033[1mAssertions:\033[0m")
    passed = 0
    failed = 0

    checks = [
        ("merchant", rec.merchant, "Uber", False),
        ("total", rec.total, 24.18, True),
        ("currency", rec.currency, "USD", False),
        ("category (heuristic should override Misc)", rec.category, "Travel — Rideshare", False),
        ("last_4", rec.last_4, "4392", False),
        ("payment_method_kind", rec.payment_method_kind, "Visa", False),
        ("date", rec.date, "2026-04-26", False),
        ("source_id propagated", rec.source_id, "live_smoke_test", False),
        ("source_kind", rec.source_kind, "email_text", False),
    ]
    for label, actual, expected, tolerate in checks:
        if assert_eq(label, actual, expected, tolerate):
            passed += 1
        else:
            failed += 1

    # Confidence threshold check (advisory, not strict)
    print()
    if rec.confidence >= 0.8:
        print(f"\033[32m  ✓\033[0m confidence: {rec.confidence:.2f} (high — production-ready)")
    elif rec.confidence >= 0.5:
        print(f"\033[33m  ⚠\033[0m confidence: {rec.confidence:.2f} (moderate — review samples)")
    else:
        print(f"\033[31m  ✗\033[0m confidence: {rec.confidence:.2f} (low — prompt may need tuning)")
        failed += 1

    # 4. Summary
    print()
    print("\033[1m" + "=" * 70 + "\033[0m")
    if failed == 0:
        print(f"\033[32m\033[1mPASS — {passed}/{passed+failed} assertions\033[0m")
    else:
        print(f"\033[31m\033[1mFAIL — {failed} assertion(s) failed, {passed} passed\033[0m")
    print("\033[1m" + "=" * 70 + "\033[0m\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
