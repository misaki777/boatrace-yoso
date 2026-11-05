"""組合せ確率の計算(エクスポート側の窓口)。

本来の計算は モデル担当の `src/model/combinations.py` が持つ(spec 03 2.2章、06 2章の契約3)。
export は p_win から券種ごとの確率を計算し直す(spec 03 7章)。

ここでは次の方針をとる:
- 通常は `src.model.combinations.combination_probs(p_win, bet_types=...)` を呼ぶ
  (戻り値は {"win": {"1": p, ...}, "exacta": {"1-2": p, ...}, "trifecta": {"1-2-3": p, ...}})。
- import できない環境(numpy が無い等)では、同じ式(Harville、γ=1)を標準ライブラリだけで計算する
  内部実装を使う。内部実装は make_sample(標準ライブラリのみで動かしたい)でも使う。
  両者が一致することは tests/export/test_build.py で確認している。

枠番は 1〜6。p_win は枠番の順の長さ6の列。
"""

from __future__ import annotations

from itertools import permutations
from typing import Iterable, Sequence

BOATS = (1, 2, 3, 4, 5, 6)

# export が使う券種(spec 03 6章: MVPは単勝・2連単・3連単)
EXPORT_BET_TYPES = ("win", "exacta", "trifecta")


def normalize(p_win: Sequence[float], absent: Iterable[int] = ()) -> list[float]:
    """欠場艇(枠番)の確率を0にして、残りの合計が1になるように正規化し直す。"""
    absent_set = set(absent)
    values = [0.0 if (i + 1) in absent_set else max(float(p), 0.0) for i, p in enumerate(p_win)]
    total = sum(values)
    if total <= 0:
        raise ValueError("正規化できない確率です(合計が0)")
    return [v / total for v in values]


def _internal_harville(p_win: Sequence[float]) -> dict[str, dict[str, float]]:
    """Harville(γ=1)で単勝・2連単・3連単の確率を計算する(標準ライブラリのみ)。"""
    p = list(p_win)
    if len(p) != 6:
        raise ValueError("p_win は6艇分必要です")
    win = {str(i): p[i - 1] for i in BOATS}
    exacta: dict[str, float] = {}
    trifecta: dict[str, float] = {}
    for i, j in permutations(BOATS, 2):
        pi, pj = p[i - 1], p[j - 1]
        exacta[f"{i}-{j}"] = pi * pj / (1 - pi) if pi < 1 else 0.0
    for i, j, k in permutations(BOATS, 3):
        pi, pj, pk = p[i - 1], p[j - 1], p[k - 1]
        d1 = 1 - pi
        d2 = 1 - pi - pj
        trifecta[f"{i}-{j}-{k}"] = pi * pj / d1 * pk / d2 if d1 > 0 and d2 > 0 else 0.0
    return {"win": win, "exacta": exacta, "trifecta": trifecta}


def _model_backend():
    """モデル担当の combinations.combination_probs があれば返す。無ければ None。"""
    try:
        from src.model import combinations as mc  # type: ignore
    except Exception:  # noqa: BLE001 - 未作成・依存の不足はすべて内部実装に切り替える
        return None
    return getattr(mc, "combination_probs", None)


def combination_probs(p_win: Sequence[float], use_model: bool = True, gamma2: float = 1.0,
                      gamma3: float = 1.0) -> dict[str, dict[str, float]]:
    """p_win から、export で使う券種(win / exacta / trifecta)の確率を返す。

    gamma2 / gamma3 は Harville の補正(spec 03 2.2章)。meta.json の harville_gamma の値を渡す。
    内部実装は γ=1 だけに対応する(γ≠1 で combinations.py が使えないときはエラー)。

    戻り値: {"win": {"1": p, ...}, "exacta": {"1-2": p, ...}, "trifecta": {"1-2-3": p, ...}}
    """
    backend = _model_backend() if use_model else None
    if backend is not None:
        result = backend(list(p_win), gamma2=float(gamma2), gamma3=float(gamma3), bet_types=EXPORT_BET_TYPES)
        return {bt: {str(c): float(v) for c, v in dict(result[bt]).items()} for bt in EXPORT_BET_TYPES}
    if gamma2 != 1.0 or gamma3 != 1.0:
        raise RuntimeError("γ補正には src/model/combinations.py が必要です")
    return _internal_harville(p_win)


def top2_top3(trifecta: dict[str, float]) -> tuple[list[float], list[float]]:
    """3連単の確率から、各艇の2着以内・3着以内の確率を出す。"""
    top2 = [0.0] * 6
    top3 = [0.0] * 6
    for comb, prob in trifecta.items():
        boats = [int(x) for x in comb.split("-")]
        for pos, b in enumerate(boats):
            top3[b - 1] += prob
            if pos < 2:
                top2[b - 1] += prob
    return top2, top3
