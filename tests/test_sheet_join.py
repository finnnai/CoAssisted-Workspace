# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the join-across-sheets primitive."""

from __future__ import annotations

import sheet_join as sj


# --------------------------------------------------------------------------- #
# Engine + Query basics
# --------------------------------------------------------------------------- #


def test_register_and_query_returns_rows():
    eng = sj.Engine()
    eng.register("a", [{"x": 1}, {"x": 2}])
    assert eng.query("a").count() == 2


def test_query_filter():
    eng = sj.Engine()
    eng.register("a", [{"x": 1}, {"x": 2}, {"x": 3}])
    rows = eng.query("a").filter(lambda r: r["x"] >= 2).rows()
    assert len(rows) == 2


def test_query_where_shorthand():
    eng = sj.Engine()
    eng.register("a", [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}])
    rows = eng.query("a").where(y="b").rows()
    assert len(rows) == 1
    assert rows[0]["x"] == 2


def test_query_project_drops_extra_cols():
    eng = sj.Engine()
    eng.register("a", [{"x": 1, "y": 2, "z": 3}])
    rows = eng.query("a").project("x", "z").rows()
    assert rows[0] == {"x": 1, "z": 3}


def test_query_order_by_desc():
    eng = sj.Engine()
    eng.register("a", [{"x": 1}, {"x": 3}, {"x": 2}])
    out = eng.query("a").order_by("x", desc=True).pluck("x")
    assert out == [3, 2, 1]


# --------------------------------------------------------------------------- #
# Joins
# --------------------------------------------------------------------------- #


def test_inner_join_basic():
    eng = sj.Engine()
    eng.register("invoices", [
        {"id": 1, "vendor_id": 10, "total": 100},
        {"id": 2, "vendor_id": 11, "total": 200},
    ])
    eng.register("vendors", [
        {"vendor_id": 10, "name": "Acme"},
        {"vendor_id": 11, "name": "Beta"},
        {"vendor_id": 12, "name": "Orphan"},
    ])
    out = (eng.query("invoices")
           .inner_join(eng.query("vendors"), left="vendor_id")
           .rows())
    assert len(out) == 2
    assert {r["name"] for r in out} == {"Acme", "Beta"}


def test_left_join_keeps_unmatched():
    eng = sj.Engine()
    eng.register("a", [{"k": 1}, {"k": 2}, {"k": 3}])
    eng.register("b", [{"k": 1, "v": "x"}])
    out = (eng.query("a").left_join(eng.query("b"), left="k").rows())
    assert len(out) == 3


def test_inner_join_with_collision_uses_suffix():
    eng = sj.Engine()
    eng.register("a", [{"k": 1, "name": "fromA"}])
    eng.register("b", [{"k": 1, "name": "fromB"}])
    out = (eng.query("a").inner_join(eng.query("b"), left="k", suffix="_r").rows())
    assert out[0]["name"] == "fromA"
    assert out[0]["name_r"] == "fromB"


# --------------------------------------------------------------------------- #
# group_by + agg
# --------------------------------------------------------------------------- #


def test_group_by_with_sum_agg():
    eng = sj.Engine()
    eng.register("a", [
        {"k": "x", "v": 10}, {"k": "x", "v": 20}, {"k": "y", "v": 5},
    ])
    out = (eng.query("a")
           .group_by("k")
           .agg(total=lambda rows: sum(r["v"] for r in rows),
                count=lambda rows: len(rows))
           .rows())
    by_key = {r["k"]: r for r in out}
    assert by_key["x"]["total"] == 30
    assert by_key["x"]["count"] == 2
    assert by_key["y"]["total"] == 5


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_safe_float_handles_strings_and_dollars():
    assert sj.safe_float("$1,234.56") == 1234.56
    assert sj.safe_float("(500)") == -500.0
    assert sj.safe_float(None) == 0.0
    assert sj.safe_float("") == 0.0
    assert sj.safe_float("not a number") == 0.0


def test_parse_date_iso_and_us():
    assert sj.parse_date("2026-04-28") == "2026-04-28"
    assert sj.parse_date("4/28/2026") == "2026-04-28"
    assert sj.parse_date("4/28/26") == "2026-04-28"
    assert sj.parse_date("2026-04-28T10:00:00") == "2026-04-28"
    assert sj.parse_date(None) is None


# --------------------------------------------------------------------------- #
# Statistical helpers
# --------------------------------------------------------------------------- #


def test_iqr_returns_quartiles():
    q1, med, q3 = sj.iqr([1, 2, 3, 4, 5, 6, 7, 8])
    assert q1 < med < q3


def test_is_outlier_iqr():
    # Distribution: median ~4-5
    values = [1, 2, 3, 4, 5, 6, 7, 8]
    q1, _, q3 = sj.iqr(values)
    assert sj.is_outlier_iqr(100, q1, q3) is True
    assert sj.is_outlier_iqr(5, q1, q3) is False
