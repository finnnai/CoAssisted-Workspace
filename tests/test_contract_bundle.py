# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for contract bundle pure-logic core."""

from __future__ import annotations

import contract_bundle as core


def _f(name: str, mime: str = "application/pdf",
       modified: str = "2025-06-15T10:00:00Z",
       file_id: str = "x",
       link: str = "https://drive.example/x") -> dict:
    return {
        "id": file_id, "name": name, "mimeType": mime,
        "modifiedTime": modified, "webViewLink": link,
    }


# --------------------------------------------------------------------------- #
# looks_like_contract
# --------------------------------------------------------------------------- #


def test_filename_with_nda_matches():
    assert core.looks_like_contract("Acme NDA signed.pdf", "application/pdf")


def test_filename_with_msa_matches():
    assert core.looks_like_contract("MSA - Anthropic.pdf", "application/pdf")


def test_filename_no_keyword_no_match():
    assert not core.looks_like_contract("invoice_2025.pdf", "application/pdf")


def test_folder_mime_rejected():
    assert not core.looks_like_contract(
        "NDA Folder", "application/vnd.google-apps.folder",
    )


def test_signed_keyword_matches():
    assert core.looks_like_contract("Acme_signed.pdf", "application/pdf")


def test_executed_keyword_matches():
    assert core.looks_like_contract("Acme executed final.docx",
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# --------------------------------------------------------------------------- #
# extract_counterparty
# --------------------------------------------------------------------------- #


def test_counterparty_simple():
    assert core.extract_counterparty("NDA - Acme Corp - 2025.pdf") == "Acme Corp"


def test_counterparty_underscore():
    assert core.extract_counterparty("Acme_NDA_signed.pdf") == "Acme"


def test_counterparty_with_date_prefix():
    cp = core.extract_counterparty("2025-09-12 MSA Anthropic v3 (executed).pdf")
    assert cp == "Anthropic"


def test_counterparty_returns_none_for_pure_noise():
    assert core.extract_counterparty("nda agreement signed.pdf") is None


# --------------------------------------------------------------------------- #
# filter_contracts
# --------------------------------------------------------------------------- #


def test_filter_year_in_modified_time():
    files = [
        _f("NDA Acme.pdf", modified="2024-01-01T00:00:00Z"),
        _f("NDA Beta.pdf", modified="2025-06-15T00:00:00Z"),
        _f("NDA Gamma.pdf", modified="2026-04-01T00:00:00Z"),
    ]
    out = core.filter_contracts(files, year=2025)
    assert len(out) == 1
    assert out[0].name == "NDA Beta.pdf"


def test_filter_by_contract_type():
    files = [
        _f("NDA Acme.pdf"),
        _f("MSA Beta.pdf"),
        _f("SOW Gamma.pdf"),
    ]
    out = core.filter_contracts(files, contract_type="MSA")
    assert len(out) == 1
    assert out[0].name == "MSA Beta.pdf"


def test_filter_type_all_keeps_everything():
    files = [_f("NDA Acme.pdf"), _f("MSA Beta.pdf")]
    out = core.filter_contracts(files, contract_type="all")
    assert len(out) == 2


def test_filter_skips_non_contracts():
    files = [
        _f("NDA Acme.pdf"),
        _f("invoice_2025.pdf"),                 # rejected — no keyword
        _f("NDA Folder", mime="application/vnd.google-apps.folder"),  # folder
    ]
    out = core.filter_contracts(files)
    assert len(out) == 1
    assert out[0].name == "NDA Acme.pdf"


def test_filter_sorts_by_modified_desc():
    files = [
        _f("NDA Old.pdf", modified="2024-01-01T00:00:00Z"),
        _f("NDA New.pdf", modified="2026-04-01T00:00:00Z"),
        _f("NDA Mid.pdf", modified="2025-08-01T00:00:00Z"),
    ]
    out = core.filter_contracts(files)
    names = [f.name for f in out]
    assert names == ["NDA New.pdf", "NDA Mid.pdf", "NDA Old.pdf"]


# --------------------------------------------------------------------------- #
# build_index_markdown
# --------------------------------------------------------------------------- #


def test_index_markdown_has_table_with_files():
    files = core.filter_contracts([
        _f("NDA Acme.pdf"),
        _f("NDA Beta.pdf"),
    ])
    bundle = core.ContractBundle(
        title="Test bundle", year=2025, contract_type="NDA", files=files,
    )
    md = core.build_index_markdown(bundle)
    assert "Test bundle" in md
    assert "NDA Acme.pdf" in md
    assert "NDA Beta.pdf" in md
    assert "| # | Counterparty | Filename" in md


def test_index_markdown_with_no_files():
    bundle = core.ContractBundle(title="Empty", year=None, contract_type=None)
    md = core.build_index_markdown(bundle)
    assert "0 document(s)" in md
