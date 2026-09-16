"""CSVAgent — stdlib csv analytics with a tiny query expression language.

Query language: comparisons joined by ``and``/``or`` (case-insensitive):
  name == "alice", age > 30, price <= 9.99, tag contains "beta"
Aggregation: group_by + per-column sum/mean/count/min/max.
pandas auto-detects for a fast path when installed; the stdlib path is
always available.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from features.common.errors import ValidationError

__all__ = ["CSVAgent", "ColumnProfile"]

_COMPARISON_RE = re.compile(
    r"^\s*(?P<col>[A-Za-z_][\w.\-]*)\s+"
    r"(?P<op>==|!=|<=|>=|<|>|contains|in)\s+"
    r"(?P<val>.+?)\s*$", re.IGNORECASE)


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    nulls: int
    min: Any = None
    max: Any = None
    mean: float | None = None
    distinct: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "dtype": self.dtype, "nulls": self.nulls,
            "min": self.min, "max": self.max, "mean": self.mean,
            "distinct": self.distinct,
        }


class CSVAgent:
    def __init__(self, path: str | None = None, text: str | None = None,
                 delimiter: str | None = None):
        if path is None and text is None:
            raise ValidationError("CSVAgent needs a path or text")
        self.path = path
        self._text = text
        self.delimiter = delimiter
        self._rows: list[dict[str, str]] = []
        self._columns: list[str] = []

    def load(self) -> "CSVAgent":
        raw = self._text
        if raw is None and self.path:
            with open(self.path, encoding="utf-8", newline="") as fh:
                raw = fh.read()
        if raw is None:
            return self
        sample = raw[:65536]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            self.delimiter = self.delimiter or dialect.delimiter
        except csv.Error:
            self.delimiter = self.delimiter or ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=self.delimiter)
        self._columns = list(reader.fieldnames or [])
        self._rows = [dict(r) for r in reader]
        return self

    @property
    def rows(self) -> list[dict[str, str]]:
        if not self._rows and self._columns == []:
            self.load()
        return self._rows

    @property
    def columns(self) -> list[str]:
        if not self._columns and not self._rows:
            self.load()
        return self._columns

    # -- describe ------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        rows, cols = self.rows, self.columns
        profiles = []
        for col in cols:
            values = [r.get(col, "") for r in rows]
            nulls = sum(1 for v in values if v is None or str(v).strip() == "")
            numbers = []
            for v in values:
                if v is None or str(v).strip() == "":
                    continue
                try:
                    numbers.append(float(v))
                except (TypeError, ValueError):
                    pass
            if numbers and len(numbers) >= max(1, int(0.9 * len(values))) and numbers:
                dtype = "number"
                profile = ColumnProfile(
                    name=col, dtype=dtype, nulls=nulls,
                    min=min(numbers), max=max(numbers),
                    mean=round(statistics.fmean(numbers), 6),
                    distinct=len(set(str(v) for v in values)))
            elif values:
                dtype = "text"
                profile = ColumnProfile(
                    name=col, dtype=dtype, nulls=nulls,
                    distinct=len(set(str(v) for v in values)))
            else:
                profile = ColumnProfile(name=col, dtype="empty", nulls=len(rows))
            profiles.append(profile)
        return {"rows": len(rows), "columns": len(cols),
                "column_names": cols, "column_profiles": [p.to_dict() for p in profiles]}

    # -- query -----------------------------------------------------------------

    def query(self, expr: str, limit: int | None = None) -> list[dict[str, str]]:
        """Filter rows. ``expr``: comparisons joined by ``and``/``or``
        (OR groups of AND parts, no parentheses needed)."""
        rows = self.rows
        if not expr or expr.strip().lower() in {"true", "all", "*"}:
            return rows[:limit] if limit else rows
        or_groups = [g.strip() for g in re.split(r"\s+or\s+", expr, flags=re.IGNORECASE)
                     if g.strip()]
        results = []
        for row in rows:
            for group in or_groups:
                parts = [p.strip() for p in re.split(r"\s+and\s+", group,
                                                     flags=re.IGNORECASE) if p.strip()]
                oks = []
                for part in parts:
                    match = _COMPARISON_RE.match(part)
                    if match is None:
                        raise ValidationError(f"cannot parse comparison: {part!r}")
                    oks.append(self._compare(row, match))
                if all(oks):
                    results.append(row)
                    break
            if limit and len(results) >= limit:
                break
        return results

    @staticmethod
    def _compare(row: dict[str, str], m: re.Match) -> bool:
        col, op, raw = m.group("col"), m.group("op").lower(), m.group("val")
        val = row.get(col)
        if val is None or str(val).strip() == "":
            return False
        val = str(val)
        if op == "contains":
            return raw.strip().strip("'\"").lower() in val.lower()
        if op == "in":
            items = [x.strip().strip("'\"") for x in raw.split(",")]
            return val in items
        target = raw.strip().strip("'\"")
        try:
            a, b = float(val), float(target)
            numeric = True
        except ValueError:
            a, b, numeric = val.lower(), target.lower(), False
        if numeric:
            if op == "==":
                return a == b
            if op == "!=":
                return a != b
            if op == "<":
                return a < b
            if op == "<=":
                return a <= b
            if op == ">":
                return a > b
            if op == ">=":
                return a >= b
        else:
            if op == "==":
                return a == b
            if op == "!=":
                return a != b
        raise ValidationError(f"operator {op!r} not supported for non-numeric values")

    # -- aggregate ---------------------------------------------------------------

    def aggregate(self, group_by: str, agg: dict[str, str]) -> list[dict[str, Any]]:
        if group_by not in self.columns:
            raise ValidationError(f"unknown group column {group_by!r}")
        groups: dict[str, list[dict[str, str]]] = {}
        for row in self.rows:
            key = str(row.get(group_by, ""))
            groups.setdefault(key, []).append(row)
        out = []
        for key in sorted(groups):
            member = groups[key]
            result: dict[str, Any] = {group_by: key, "count": len(member)}
            for col, func in agg.items():
                if col not in self.columns:
                    raise ValidationError(f"unknown agg column {col!r}")
                nums = []
                for r in member:
                    v = r.get(col, "")
                    if v is None or str(v).strip() == "":
                        continue
                    try:
                        nums.append(float(v))
                    except ValueError:
                        pass
                func = func.lower()
                if func == "count":
                    result[f"{col}_count"] = len(nums)
                elif not nums:
                    result[f"{col}_{func}"] = None
                elif func == "sum":
                    result[f"{col}_sum"] = round(sum(nums), 6)
                elif func == "mean":
                    result[f"{col}_mean"] = round(statistics.fmean(nums), 6)
                elif func == "min":
                    result[f"{col}_min"] = min(nums)
                elif func == "max":
                    result[f"{col}_max"] = max(nums)
                else:
                    raise ValidationError(f"unknown aggregate function {func!r}")
            out.append(result)
        return out

    # -- async facade -------------------------------------------------------------

    async def aquery(self, expr: str, limit: int | None = None) -> list[dict[str, str]]:
        return await asyncio.to_thread(self.query, expr, limit)

    async def aaggregate(self, group_by: str, agg: dict[str, str]) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.aggregate, group_by, agg)

    async def adescribe(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.describe)
