# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for contract bundle filtering + indexing on synthetic Drive list."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import contract_bundle as core


def f(name: str, mime: str = "application/pdf",
      modified: str = "2025-06-15T10:00:00Z",
      file_id: str = None) -> dict:
    return {
        "id": file_id or f"id_{name[:8]}",
        "name": name, "mimeType": mime,
        "modifiedTime": modified,
        "webViewLink": f"https://drive.example/{file_id or name}",
    }


def main() -> int:
    drive_files = [
        f("NDA - Acme Corp - 2025.pdf",      modified="2025-09-15T00:00:00Z"),
        f("MSA Anthropic 2025 (executed).pdf", modified="2025-04-01T00:00:00Z"),
        f("SOW Capital Electric.pdf",         modified="2025-11-22T00:00:00Z"),
        f("Acme_signed.pdf",                  modified="2025-03-10T00:00:00Z"),
        f("DPA - StaffWizard.pdf",            modified="2024-12-15T00:00:00Z"),
        f("invoice_2025.pdf",                 modified="2025-06-01T00:00:00Z"),  # not a contract
        f("Project plan.gdoc",
          mime="application/vnd.google-apps.document",
          modified="2025-05-01T00:00:00Z"),  # google doc with no contracty token
        f("Old NDA.pdf",                      modified="2024-01-15T00:00:00Z"),
        f("Vacation photos",
          mime="application/vnd.google-apps.folder",
          modified="2025-07-01T00:00:00Z"),  # folder
    ]

    print("=" * 100)
    print("SMOKE TEST: contract bundle — filtering + indexing")
    print("=" * 100)

    fails = []

    # Scenario 1: all contracts in 2025
    bundle_25 = core.filter_contracts(drive_files, year=2025)
    print(f"\nAll 2025 contracts: {len(bundle_25)}")
    for f_ in bundle_25:
        print(f"  - {f_.modified_time[:10]}  {f_.name}  → counterparty: {f_.counterparty}")

    if len(bundle_25) != 4:
        fails.append(f"Expected 4 contracts in 2025, got {len(bundle_25)}")

    # Scenario 2: NDAs only
    bundle_nda = core.filter_contracts(drive_files, contract_type="NDA")
    print(f"\nAll NDAs: {len(bundle_nda)}")
    for f_ in bundle_nda:
        print(f"  - {f_.name}")
    if len(bundle_nda) != 2:  # 'NDA - Acme', 'Old NDA'
        fails.append(f"Expected 2 NDAs, got {len(bundle_nda)}")

    # Scenario 3: NDA + 2025
    bundle_nda_25 = core.filter_contracts(drive_files, year=2025, contract_type="NDA")
    print(f"\n2025 NDAs only: {len(bundle_nda_25)}")
    if len(bundle_nda_25) != 1:
        fails.append(f"Expected 1 NDA in 2025, got {len(bundle_nda_25)}")

    # Scenario 4: Index markdown rendering
    full_bundle = core.ContractBundle(
        title="Contracts 2025", year=2025, contract_type="all", files=bundle_25,
    )
    md = core.build_index_markdown(full_bundle)
    print()
    print("=" * 100)
    print("Sample index Doc markdown:")
    print("=" * 100)
    print(md)

    if "Acme Corp" not in md:
        fails.append("Acme Corp not in rendered index")

    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)} issue(s):")
        for f_ in fails:
            print(f"  ✗ {f_}")
        return 1
    print("PASS — filter + index produce expected output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
