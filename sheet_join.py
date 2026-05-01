# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Join-across-sheets primitive — lite SQL-on-sheets query engine.

Provides a small relational layer over sheet rows so AP analytics
(spend dashboard, P&L, dup detection, anomaly detection) can mix
project / vendor / receipt / invoice data without each workflow
reinventing the same merge logic.

Concepts:

  Table:  named collection of dict rows. Schema is implicit (the union
          of keys present on rows).

  Engine: registry of tables. Supports filter, project, group_by, agg,
          inner_join, left_join.

  Query:  fluent API that lazily composes the above operations.

This is NOT a full SQL engine. It's just enough relational algebra to
make the AP analytics workflows read cleanly. Stays in-memory; never
hits Sheets directly (the wrapper does that).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional


# --------------------------------------------------------------------------- #
# Table + Engine
# --------------------------------------------------------------------------- #


@dataclass
class Table:
    """A named, in-memory list of dict rows."""
    name: str
    rows: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)


class Engine:
    """Holds tables + lets you query them."""

    def __init__(self):
        self._tables: dict[str, Table] = {}

    def register(self, name: str, rows: Iterable[dict]) -> None:
        """Register or overwrite a table."""
        if not name:
            raise ValueError("table name required")
        self._tables[name] = Table(name=name, rows=[dict(r) for r in rows])

    def get(self, name: str) -> Table:
        if name not in self._tables:
            raise KeyError(f"unknown table: {name}")
        return self._tables[name]

    def names(self) -> list[str]:
        return list(self._tables.keys())

    def query(self, table_name: str) -> "Query":
        return Query(self.get(table_name).rows)


# --------------------------------------------------------------------------- #
# Query (fluent)
# --------------------------------------------------------------------------- #


class Query:
    """Lazy chain of relational operators over a list of rows.

    Terminal calls (.rows, .first, .count, .pluck) materialize the result.
    """

    def __init__(self, source: list[dict]):
        self._source = source

    # -- non-terminal ops ------------------------------------------------- #

    def filter(self, predicate: Callable[[dict], bool]) -> "Query":
        return Query([r for r in self._source if predicate(r)])

    def where(self, **kv) -> "Query":
        """Shorthand for equality filters: where(project='A', status='OPEN')."""
        def pred(r):
            return all(r.get(k) == v for k, v in kv.items())
        return self.filter(pred)

    def project(self, *cols: str) -> "Query":
        """Drop all columns except the named ones."""
        return Query([{c: r.get(c) for c in cols} for r in self._source])

    def map(self, fn: Callable[[dict], dict]) -> "Query":
        return Query([fn(dict(r)) for r in self._source])

    def order_by(self, key: str, *, desc: bool = False) -> "Query":
        return Query(sorted(self._source, key=lambda r: r.get(key) or "", reverse=desc))

    def limit(self, n: int) -> "Query":
        return Query(list(self._source[:n]))

    def inner_join(self, other: "Query | list[dict]",
                   *, left: str, right: str | None = None,
                   suffix: str = "_right") -> "Query":
        """Inner join on a single key. `right` defaults to `left`."""
        right = right or left
        right_rows = other._source if isinstance(other, Query) else list(other)
        right_index: dict[Any, list[dict]] = {}
        for r in right_rows:
            right_index.setdefault(r.get(right), []).append(r)
        out: list[dict] = []
        for l in self._source:
            for r in right_index.get(l.get(left), []):
                merged = dict(l)
                for k, v in r.items():
                    if k in merged and k != right:
                        merged[f"{k}{suffix}"] = v
                    else:
                        merged[k] = v
                out.append(merged)
        return Query(out)

    def left_join(self, other: "Query | list[dict]",
                  *, left: str, right: str | None = None,
                  suffix: str = "_right") -> "Query":
        right = right or left
        right_rows = other._source if isinstance(other, Query) else list(other)
        right_index: dict[Any, list[dict]] = {}
        for r in right_rows:
            right_index.setdefault(r.get(right), []).append(r)
        out: list[dict] = []
        for l in self._source:
            matches = right_index.get(l.get(left), [])
            if not matches:
                out.append(dict(l))
                continue
            for r in matches:
                merged = dict(l)
                for k, v in r.items():
                    if k in merged and k != right:
                        merged[f"{k}{suffix}"] = v
                    else:
                        merged[k] = v
                out.append(merged)
        return Query(out)

    def group_by(self, *keys: str) -> "GroupedQuery":
        groups: dict[tuple, list[dict]] = {}
        for r in self._source:
            gk = tuple(r.get(k) for k in keys)
            groups.setdefault(gk, []).append(r)
        return GroupedQuery(keys=keys, groups=groups)

    # -- terminal ops ----------------------------------------------------- #

    def rows(self) -> list[dict]:
        return [dict(r) for r in self._source]

    def first(self) -> Optional[dict]:
        return dict(self._source[0]) if self._source else None

    def count(self) -> int:
        return len(self._source)

    def pluck(self, key: str) -> list[Any]:
        return [r.get(key) for r in self._source]


@dataclass
class GroupedQuery:
    keys: tuple[str, ...]
    groups: dict[tuple, list[dict]]

    def agg(self, **specs) -> Query:
        """Aggregate per group. specs = field_name: callable(rows) -> value.

        Example:
            grouped.agg(
                total_spend=lambda rows: sum(r['total'] or 0 for r in rows),
                count=lambda rows: len(rows),
            )
        """
        out: list[dict] = []
        for gk, rows in self.groups.items():
            row = {k: v for k, v in zip(self.keys, gk)}
            for col, fn in specs.items():
                row[col] = fn(rows)
            out.append(row)
        return Query(out)


# --------------------------------------------------------------------------- #
# Helpers commonly used by AP analytics
# --------------------------------------------------------------------------- #


def safe_float(value: Any) -> float:
    """Convert a sheet cell to float, tolerating $-prefixed and comma'd strings."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    s = re.sub(r"[$,\s]", "", s)
    s = s.replace("(", "-").replace(")", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date(value: Any) -> Optional[str]:
    """Normalize a date cell to YYYY-MM-DD or return None."""
    if not value:
        return None
    s = str(value).strip()
    # Already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # MM/DD/YYYY or M/D/YY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        mm, dd, yy = m.groups()
        if len(yy) == 2:
            yy = "20" + yy
        return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
    # ISO datetime → strip time
    if "T" in s and len(s) >= 10:
        return s[:10]
    return None


def date_to_int(date_str: str | None) -> int:
    """For sortable comparisons. None → 0."""
    if not date_str:
        return 0
    try:
        return int(date_str.replace("-", ""))
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# Statistical helpers
# --------------------------------------------------------------------------- #


def median(values: Iterable[float]) -> float:
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0
    return statistics.median(vals)


def iqr(values: Iterable[float]) -> tuple[float, float, float]:
    """Return (q1, median, q3) for the value list."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return (0.0, 0.0, 0.0)
    q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [vals[0], statistics.median(vals), vals[-1]]
    return (q[0], statistics.median(vals), q[2])


def is_outlier_iqr(value: float, q1: float, q3: float, k: float = 1.5) -> bool:
    """Tukey's fences. Outlier if value < q1 - k*IQR or value > q3 + k*IQR."""
    iqr_range = q3 - q1
    if iqr_range <= 0:
        return False
    return value < q1 - k * iqr_range or value > q3 + k * iqr_range
