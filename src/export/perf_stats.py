"""成績の集計(較正・戦略比較)。spec 08 5.3、04 8.2。

区間・連敗・ドローダウン・sample_note の計算はモデル担当(M-11)の src/model/summary.py と backtest.py の関数を
そのまま使う(08 5.3「計算を二重に書かない」)。ここは公開JSON(days)の値を、その関数に渡す形に並べるだけ。
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from src.model import backtest as bt
from src.model import summary as ms


def wilson_ci(k: int, n: int) -> tuple[float, float] | None:
    ci = ms.wilson_ci(int(k), int(n))
    return tuple(ci) if ci is not None else None


def bootstrap_roi_ci(stakes: Sequence[int], returns: Sequence[int]) -> tuple[float, float] | None:
    if not stakes or sum(stakes) == 0:
        return None
    ci = bt.bootstrap_roi_ci(np.asarray(stakes, dtype=float), np.asarray(returns, dtype=float))
    return tuple(ci) if ci is not None else None


def max_losing_streak(hits: Sequence[bool]) -> int:
    return int(ms.max_losing_streak(list(hits)))


def max_drawdown(profits: Sequence[float]) -> float:
    return float(ms.max_drawdown_yen(list(profits)))


def sample_note(races: int) -> str | None:
    return ms.sample_note(int(races))


def calibration_bins(pairs: Sequence[tuple[float, bool]]) -> list[dict[str, Any]]:
    """(1着確率, 1着になったか) の組から、10%刻みの帯ごとの較正表(件数0の帯は出さない)。"""
    bins: dict[int, list] = {}
    for p, won in pairs:
        b = min(int(p * 10), 9)
        bins.setdefault(b, []).append((p, won))
    out = []
    for b in sorted(bins):
        xs = bins[b]
        n = len(xs)
        k = sum(1 for _, w in xs if w)
        ci = wilson_ci(k, n)
        out.append({"bin": f"{b * 10}-{b * 10 + 10}", "n": n, "pred_mean": round(sum(p for p, _ in xs) / n, 3),
                    "actual": round(k / n, 3), "ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None})
    return out


def strategy_stats(per_race: Sequence[tuple[int, int, int, bool]], *, seed: int = 0) -> dict[str, Any]:
    """レースごとの (点数, 投資, 払戻, 的中したか) から戦略比較の値。点数0(見送り)のレースは数えない。"""
    rows = [r for r in per_race if r[0] > 0]
    stakes = [r[1] for r in rows]
    rets = [r[2] for r in rows]
    stake, ret = sum(stakes), sum(rets)
    ci = bootstrap_roi_ci(stakes, rets)
    return {
        "races": len(rows), "bets": sum(r[0] for r in rows),
        "roi": round(ret / stake, 3) if stake else None,
        "roi_ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None,
        "hit_rate": round(sum(1 for r in rows if r[3]) / len(rows), 3) if rows else None,
        "max_losing_streak": max_losing_streak([r[3] for r in rows]),
        "max_drawdown_yen": int(round(max_drawdown([r[2] - r[1] for r in rows]))),
    }
