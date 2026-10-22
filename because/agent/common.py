"""Small numeric + formatting helpers shared by the playbooks."""

from __future__ import annotations

import math
from datetime import date
from typing import Any


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    s = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.1f}M"
    return f"{s}${a:,.0f}"


def pct(a: float | None, b: float | None) -> float | None:
    """Percent change from b to a."""
    if a is None or b in (None, 0):
        return None
    return (a - b) / abs(b) * 100.0


def fmt_pct(v: float | None, signed: bool = True, dp: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.{dp}f}%" if signed else f"{v:.{dp}f}%"


def fmt_pts(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f} pts"


def days_between(a: str | None, b: str | None) -> int | None:
    try:
        y1, m1, d1 = map(int, a[:10].split("-"))
        y2, m2, d2 = map(int, b[:10].split("-"))
        return (date(y2, m2, d2) - date(y1, m1, d1)).days
    except Exception:
        return None


def fiscal_label(period_end: str) -> int:
    """52/53-week years that end in the first week of January belong to the prior year."""
    y, m, d = int(period_end[:4]), int(period_end[5:7]), int(period_end[8:10])
    return y - 1 if (m == 1 and d <= 7) else y


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 6:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def month_key(period: str) -> str:
    return period[:7]


def shift_month(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def confidence_label(score: float) -> str:
    return "High" if score >= 0.7 else ("Medium" if score >= 0.45 else "Low")


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def first(xs: list[Any], default: Any = None) -> Any:
    return xs[0] if xs else default
