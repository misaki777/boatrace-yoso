"""1着確率 p_win から各券種の組合せ確率を計算する(spec/03 2.2章、5章)。

モデル担当(M)とエクスポート担当(E)の間の契約。関数のシグネチャと戻り値の形を
変えるときは PM を通すこと(spec/06 2章)。

約束事:
- p_win は「枠番1〜6の順に並んだ長さ6の配列」か「{枠番: 確率} の dict」で渡す。
  dict に無い枠番は確率0(欠場と同じ扱い)とする。
- 欠場艇は absent_entries(枠番のリスト)で渡す。その艇の p_win を0にして残りを
  正規化し直してから組合せ確率を計算する。
- 組合せの表記は API と同じ。順序あり `1-2-3`、順不同 `1=2=3`(昇順)。単勝・複勝は `"1"`。
- γ補正(Henery/Stern 型): 2着の計算では p^gamma2 を、3着の計算では p^gamma3 を
  正規化したものを使う。gamma2 = gamma3 = 1 のとき Harville 式そのものになる。
- 1着の周辺確率は γ に関係なく p_win と一致する。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations as _iter_combinations
from itertools import permutations as _iter_permutations

import numpy as np

N_BOATS = 6
BOATS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)
BET_TYPES: tuple[str, ...] = (
    "win",
    "place",
    "exacta",
    "quinella",
    "quinella_place",
    "trifecta",
    "trio",
)
# γ の探索範囲(spec/03 2.2章)。関数の引数としては正の値なら何でも受け付ける。
GAMMA_SEARCH_RANGE: tuple[float, float] = (0.5, 1.2)

# 分母がこれ以下なら「その先の着順に入る艇が無い」とみなして確率0にする
_EPS = 1e-15


# ---------------------------------------------------------------------------
# 組合せのラベル(並び順は固定。ベクトル版の列の順番と一致する)
# ---------------------------------------------------------------------------

_LABELS: dict[str, tuple[str, ...]] = {
    "win": tuple(str(i) for i in BOATS),
    "place": tuple(str(i) for i in BOATS),
    "exacta": tuple(f"{i}-{j}" for i, j in _iter_permutations(BOATS, 2)),
    "quinella": tuple(f"{i}={j}" for i, j in _iter_combinations(BOATS, 2)),
    "quinella_place": tuple(f"{i}={j}" for i, j in _iter_combinations(BOATS, 2)),
    "trifecta": tuple(f"{i}-{j}-{k}" for i, j, k in _iter_permutations(BOATS, 3)),
    "trio": tuple(f"{i}={j}={k}" for i, j, k in _iter_combinations(BOATS, 3)),
}

# 0始まりのインデックス(ベクトル計算用)
_PERM2 = np.array(list(_iter_permutations(range(N_BOATS), 2)))  # (30, 2)
_PERM3 = np.array(list(_iter_permutations(range(N_BOATS), 3)))  # (120, 3)
_COMB2 = np.array(list(_iter_combinations(range(N_BOATS), 2)))  # (15, 2)
_COMB3 = np.array(list(_iter_combinations(range(N_BOATS), 3)))  # (20, 3)


def combination_labels(bet_type: str) -> tuple[str, ...]:
    """券種の全組合せのラベルを返す(単勝・複勝6、2連単30、2連複・拡連複15、3連単120、3連複20)。

    並び順は combination_prob_matrix の列の順と同じ(枠番の辞書順)。
    """
    _check_bet_type(bet_type)
    return _LABELS[bet_type]


# ---------------------------------------------------------------------------
# 入力の整形
# ---------------------------------------------------------------------------


def normalize_p_win(
    p_win: Sequence[float] | Mapping[int, float] | np.ndarray,
    absent_entries: Iterable[int] = (),
) -> np.ndarray:
    """p_win を長さ6の配列にし、欠場艇を0にしてから合計1に正規化し直す。

    Args:
        p_win: 枠番1〜6の順の長さ6の配列、または {枠番: 確率} の dict。
        absent_entries: 欠場・返還になった枠番(1〜6)。

    Returns:
        合計が1の np.ndarray(shape=(6,), float64)。

    Raises:
        ValueError: 長さが6でない、負の値や NaN がある、正規化できない(残りの確率の合計が0)とき。
    """
    p = _to_array6(p_win)
    for entry in absent_entries:
        entry = int(entry)
        if entry not in BOATS:
            raise ValueError(f"absent_entries の枠番が不正です: {entry}")
        p[entry - 1] = 0.0
    total = p.sum()
    if not total > 0:
        raise ValueError("欠場艇を除いた p_win の合計が0のため、正規化できません")
    return p / total


def _to_array6(p_win: Sequence[float] | Mapping[int, float] | np.ndarray) -> np.ndarray:
    if isinstance(p_win, Mapping):
        p = np.zeros(N_BOATS, dtype=np.float64)
        for entry, value in p_win.items():
            entry = int(entry)
            if entry not in BOATS:
                raise ValueError(f"p_win の枠番が不正です: {entry}")
            p[entry - 1] = float(value)
    else:
        p = np.array(p_win, dtype=np.float64).reshape(-1)
        if p.shape != (N_BOATS,):
            raise ValueError(f"p_win は長さ6が必要です(実際: {p.shape[0]})")
    if not np.all(np.isfinite(p)):
        raise ValueError("p_win に NaN または無限大が含まれています")
    if np.any(p < 0):
        raise ValueError("p_win に負の値が含まれています")
    return p


def _check_matrix(p_win_matrix: np.ndarray) -> np.ndarray:
    p = np.asarray(p_win_matrix, dtype=np.float64)
    if p.ndim == 1:
        p = p[None, :]
    if p.ndim != 2 or p.shape[1] != N_BOATS:
        raise ValueError(f"p_win_matrix は (レース数, 6) が必要です(実際: {p.shape})")
    if not np.all(np.isfinite(p)) or np.any(p < 0):
        raise ValueError("p_win_matrix に NaN・無限大・負の値が含まれています")
    return p


def _check_gamma(gamma2: float, gamma3: float) -> None:
    for name, g in (("gamma2", gamma2), ("gamma3", gamma3)):
        if not (isinstance(g, (int, float)) and math.isfinite(g) and g > 0):
            raise ValueError(f"{name} は正の数が必要です: {g!r}")


def _check_bet_type(bet_type: str) -> None:
    if bet_type not in BET_TYPES:
        raise ValueError(f"未対応の券種です: {bet_type!r}(対応: {BET_TYPES})")


# ---------------------------------------------------------------------------
# ベクトル版(多数のレースをまとめて計算する。学習・評価・バックテスト用)
# ---------------------------------------------------------------------------


def _power_normalize(p: np.ndarray, gamma: float) -> np.ndarray:
    """p^gamma を行ごとに正規化する。gamma=1 ならそのまま返す(p は正規化済みの前提)。"""
    if gamma == 1.0:
        return p
    with np.errstate(divide="ignore"):
        q = np.where(p > 0, np.power(p, gamma), 0.0)
    s = q.sum(axis=1, keepdims=True)
    return np.divide(q, s, out=np.zeros_like(q), where=s > 0)


def harville_tensors(
    p_win_matrix: np.ndarray,
    gamma2: float = 1.0,
    gamma3: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """順序付きの確率テンソルを返す(Harville / Plackett-Luce、γ補正つき)。

    Args:
        p_win_matrix: shape=(レース数, 6)。各行は合計1に正規化済みであること
            (欠場艇は0にしておく。normalize_p_win を通した値を並べたもの)。
        gamma2, gamma3: 2着・3着の計算に使う指数。既定は1(補正なし)。

    Returns:
        (exacta, trifecta)
        exacta[r, i, j] = P(1着=i, 2着=j)、shape=(n, 6, 6)。i==j は0。
        trifecta[r, i, j, k] = P(1着=i, 2着=j, 3着=k)、shape=(n, 6, 6, 6)。重複は0。
        インデックスは0始まり(枠番 - 1)。
    """
    _check_gamma(gamma2, gamma3)
    p = _check_matrix(p_win_matrix)
    n = p.shape[0]
    eye = np.eye(N_BOATS, dtype=bool)

    q = _power_normalize(p, float(gamma2))  # 2着の計算に使う強さ
    r = _power_normalize(p, float(gamma3))  # 3着の計算に使う強さ

    # 2着: P(2着=j | 1着=i) = q_j / (Σq - q_i)
    q_sum = q.sum(axis=1)  # 1 か 0
    denom2 = q_sum[:, None] - q  # (n, i)
    cond2 = np.divide(
        q[:, None, :],
        denom2[:, :, None],
        out=np.zeros((n, N_BOATS, N_BOATS)),
        where=denom2[:, :, None] > _EPS,
    )
    cond2[:, eye] = 0.0
    exacta = p[:, :, None] * cond2

    # 3着: P(3着=k | 1着=i, 2着=j) = r_k / (Σr - r_i - r_j)
    r_sum = r.sum(axis=1)
    denom3 = r_sum[:, None, None] - r[:, :, None] - r[:, None, :]  # (n, i, j)
    cond3 = np.divide(
        np.broadcast_to(r[:, None, None, :], (n, N_BOATS, N_BOATS, N_BOATS)),
        denom3[:, :, :, None],
        out=np.zeros((n, N_BOATS, N_BOATS, N_BOATS)),
        where=denom3[:, :, :, None] > _EPS,
    )
    # k が i または j と同じ組合せは0
    ii, jj, kk = np.meshgrid(np.arange(6), np.arange(6), np.arange(6), indexing="ij")
    invalid = (ii == jj) | (ii == kk) | (jj == kk)
    cond3[:, invalid] = 0.0
    trifecta = exacta[:, :, :, None] * cond3
    return exacta, trifecta


def combination_prob_matrix(
    p_win_matrix: np.ndarray,
    bet_type: str,
    gamma2: float = 1.0,
    gamma3: float = 1.0,
) -> np.ndarray:
    """券種の全組合せの確率を (レース数, 組合せ数) の配列で返す。

    列の順は combination_labels(bet_type) と同じ。
    p_win_matrix の各行は正規化済み(欠場艇は0)であること。
    """
    _check_bet_type(bet_type)
    p = _check_matrix(p_win_matrix)
    if bet_type == "win":
        return p.copy()
    exacta, trifecta = harville_tensors(p, gamma2, gamma3)
    if bet_type == "exacta":
        return exacta[:, _PERM2[:, 0], _PERM2[:, 1]]
    if bet_type == "quinella":
        a, b = _COMB2[:, 0], _COMB2[:, 1]
        return exacta[:, a, b] + exacta[:, b, a]
    if bet_type == "place":
        # 2着以内に入る確率(6艇立てなので複勝は2着まで)
        return exacta.sum(axis=2) + exacta.sum(axis=1)
    if bet_type == "trifecta":
        return trifecta[:, _PERM3[:, 0], _PERM3[:, 1], _PERM3[:, 2]]
    if bet_type == "trio":
        out = np.zeros((p.shape[0], len(_COMB3)))
        for perm in _iter_permutations(range(3)):
            idx = _COMB3[:, list(perm)]
            out += trifecta[:, idx[:, 0], idx[:, 1], idx[:, 2]]
        return out
    # quinella_place: i と j がどちらも3着以内
    top3_pair = np.zeros((p.shape[0], N_BOATS, N_BOATS))
    for a, b in ((0, 1), (0, 2), (1, 2)):
        # 3連単の位置 a と b にいる2艇の組(順不同にするため両向きを足す)
        pair = trifecta.sum(axis=tuple(ax + 1 for ax in range(3) if ax not in (a, b)))
        top3_pair += pair + pair.transpose(0, 2, 1)
    a, b = _COMB2[:, 0], _COMB2[:, 1]
    return top3_pair[:, a, b]


def finish_prob_matrix(
    p_win_matrix: np.ndarray,
    gamma2: float = 1.0,
    gamma3: float = 1.0,
) -> dict[str, np.ndarray]:
    """各艇の1着・2着以内・3着以内の確率を返す(ベクトル版)。

    Returns:
        {"p_win": (n,6), "p_top2": (n,6), "p_top3": (n,6)}
    """
    p = _check_matrix(p_win_matrix)
    exacta, trifecta = harville_tensors(p, gamma2, gamma3)
    p_top2 = exacta.sum(axis=2) + exacta.sum(axis=1)
    p_top3 = p_top2 + trifecta.sum(axis=(1, 2))  # 3着になる確率を足す
    return {"p_win": p.copy(), "p_top2": p_top2, "p_top3": p_top3}


# ---------------------------------------------------------------------------
# 1レース版(エクスポート担当が呼ぶ想定)
# ---------------------------------------------------------------------------


def combination_probs(
    p_win: Sequence[float] | Mapping[int, float] | np.ndarray,
    absent_entries: Iterable[int] = (),
    gamma2: float = 1.0,
    gamma3: float = 1.0,
    bet_types: Iterable[str] = BET_TYPES,
) -> dict[str, dict[str, float]]:
    """1レース分の各券種の組合せ確率を返す。

    Args:
        p_win: 枠番1〜6の順の長さ6の配列、または {枠番: 確率}。
        absent_entries: 欠場艇の枠番。p_win を0にして正規化し直してから計算する。
        gamma2, gamma3: γ補正の指数(既定1 = Harville)。
        bet_types: 計算する券種(既定は7券種すべて)。

    Returns:
        {券種: {組合せ: 確率}}。例: {"trifecta": {"1-2-3": 0.081, ...}, "trio": {"1=2=3": ...}}
        組合せはすべて含む(欠場艇を含む組合せは確率0)。並び順は combination_labels と同じ。
    """
    p = normalize_p_win(p_win, absent_entries)[None, :]
    bet_types = tuple(bet_types)
    for bt in bet_types:
        _check_bet_type(bt)
    result: dict[str, dict[str, float]] = {}
    for bt in bet_types:
        probs = combination_prob_matrix(p, bt, gamma2, gamma3)[0]
        result[bt] = {label: float(v) for label, v in zip(_LABELS[bt], probs)}
    return result


def finish_probs(
    p_win: Sequence[float] | Mapping[int, float] | np.ndarray,
    absent_entries: Iterable[int] = (),
    gamma2: float = 1.0,
    gamma3: float = 1.0,
) -> dict[str, list[float]]:
    """1レース分の各艇の p_win・p_top2・p_top3 を返す(欠場艇の再正規化つき)。

    Returns:
        {"p_win": [6艇分], "p_top2": [...], "p_top3": [...]}(枠番1〜6の順)。
        合計はそれぞれ 1・2・3(欠場で3艇未満になった場合を除く)。
    """
    p = normalize_p_win(p_win, absent_entries)[None, :]
    out = finish_prob_matrix(p, gamma2, gamma3)
    return {key: [float(v) for v in arr[0]] for key, arr in out.items()}


def top_combinations(
    probs: Mapping[str, float],
    n: int | None = None,
) -> list[tuple[str, float]]:
    """{組合せ: 確率} を確率の降順に並べ、上位 n 件を返す(n=None なら全部)。

    確率が同じときは combination_labels の順(枠番の辞書順)で並べる(結果を毎回同じにするため)。
    """
    order = {label: idx for idx, label in enumerate(probs)}
    items = sorted(probs.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    return items if n is None else items[:n]


# ---------------------------------------------------------------------------
# 必要オッズ・期待値(spec/03 5章)
# ---------------------------------------------------------------------------


def required_odds(prob: float) -> float:
    """必要オッズ = 1 / P(c)(期待値が1になるオッズ)。prob が0以下なら inf を返す。"""
    prob = float(prob)
    if not math.isfinite(prob) or prob < 0:
        raise ValueError(f"prob が不正です: {prob!r}")
    if prob == 0:
        return math.inf
    return 1.0 / prob


def required_odds_display(prob: float) -> float:
    """表示用の必要オッズ: ceil(required_odds × 10) / 10(小数1位で切り上げ)。

    浮動小数の誤差で 1/0.05 = 20.000000000000004 が 20.1 にならないよう、
    切り上げの前に小数9桁で丸める。prob が0なら inf を返す。
    """
    odds = required_odds(prob)
    if math.isinf(odds):
        return odds
    return math.ceil(round(odds * 10, 9)) / 10


def expected_value(prob: float, odds: float) -> float:
    """期待値 EV = P(c) × odds(c)(odds は100円あたりの払戻倍率)。"""
    return float(prob) * float(odds)
