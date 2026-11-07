"""データ分析の共通ルール(spec/11 1.2〜1.4、2章。PM/統合担当が管理)。

F・M・E はこの定数と関数を import して使う。W(フロント)は同じ値を定数で持つ。
値や区分を変えるときは PM がここと spec/11 を同時に直す。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import date, datetime

from src.common.codes import wind_class

# ---------------------------------------------------------------------------
# 件数の扱い(spec/11 1.3)
# ---------------------------------------------------------------------------

MIN_COUNT_NORMAL = 200   # これ以上は通常の表示
MIN_COUNT_SHOW = 30      # これ未満は率を出さない(件数だけ)

SAMPLE_NORMAL = "normal"
SAMPLE_FEW = "few"                # 薄く表示し「参考」
SAMPLE_INSUFFICIENT = "insufficient"  # 率を出さない


def sample_level(n: int | None) -> str:
    """件数から表示区分を返す: normal / few / insufficient。"""
    if n is None or n < MIN_COUNT_SHOW:
        return SAMPLE_INSUFFICIENT
    if n < MIN_COUNT_NORMAL:
        return SAMPLE_FEW
    return SAMPLE_NORMAL


def rate_or_none(k: float, n: int | None, ndigits: int = 4) -> float | None:
    """件数が下限未満なら None、それ以外は k/n を丸めた値。公開JSONの率はこれを通す。"""
    if sample_level(n) == SAMPLE_INSUFFICIENT or not n:
        return None
    return round(k / n, ndigits)


# ---------------------------------------------------------------------------
# 区間(spec/11 1.3)
# ---------------------------------------------------------------------------

Z95 = 1.959963984540054
BOOTSTRAP_ITERATIONS = 200
BOOTSTRAP_SEED = 20261105


def wilson_interval(k: float, n: int, z: float = Z95) -> tuple[float, float] | None:
    """二項の率の Wilson 区間(95%)。n=0 なら None。"""
    if n <= 0:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"k は 0〜n の範囲で指定してください: k={k}, n={n}")
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def bootstrap_roi_interval(
    stakes: Sequence[float],
    returns: Sequence[float],
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    """レース単位のブートストラップによる回収率(Σ払戻/Σ購入)の区間。

    stakes・returns は1レースごとの購入額と払戻額(同じ長さ)。購入額の合計が0なら None。
    seed を固定しているので、同じ入力なら同じ結果になる(冪等な出力のため)。
    """
    if len(stakes) != len(returns):
        raise ValueError("stakes と returns の長さが違います")
    n = len(stakes)
    if n == 0 or sum(stakes) <= 0:
        return None
    rng = random.Random(seed)
    rois: list[float] = []
    for _ in range(iterations):
        s = r = 0.0
        for _ in range(n):
            i = rng.randrange(n)
            s += stakes[i]
            r += returns[i]
        if s > 0:
            rois.append(r / s)
    if not rois:
        return None
    rois.sort()
    lo = rois[int(math.floor(alpha / 2 * (len(rois) - 1)))]
    hi = rois[int(math.ceil((1 - alpha / 2) * (len(rois) - 1)))]
    return lo, hi


def ci_crosses(interval: tuple[float, float] | None, value: float = 1.0) -> bool:
    """区間が value(回収率なら1.0=100%)をまたぐか。"""
    return interval is not None and interval[0] <= value <= interval[1]


# ---------------------------------------------------------------------------
# 区分(spec/11 2章)
# ---------------------------------------------------------------------------

SEASONS = ("spring", "summer", "autumn", "winter")
SEASON_LABELS = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}


def season_of(d: date) -> str:
    """春 3〜5月、夏 6〜8月、秋 9〜11月、冬 12〜2月。"""
    m = d.month
    if 3 <= m <= 5:
        return "spring"
    if 6 <= m <= 8:
        return "summer"
    if 9 <= m <= 11:
        return "autumn"
    return "winter"


NIGHT_FROM_HOUR = 17  # 締切 17:00 以降をナイターとみなす


def is_night(closed_at: datetime | None) -> bool | None:
    if closed_at is None:
        return None
    return closed_at.hour >= NIGHT_FROM_HOUR


TIME_BANDS = ("morning", "day", "night")  # 〜12時、12〜17時、17時〜(自分の収支分析)


def time_band(closed_at: datetime) -> str:
    if closed_at.hour < 12:
        return "morning"
    if closed_at.hour < NIGHT_FROM_HOUR:
        return "day"
    return "night"


WIND_SPEED_BANDS = ("weak", "mid", "strong")  # 0〜2m、3〜5m、6m以上


def wind_speed_band(speed: float | None) -> str | None:
    if speed is None:
        return None
    if speed <= 2:
        return "weak"
    if speed <= 5:
        return "mid"
    return "strong"


def wind_bucket(direction: int | None, speed: float | None) -> str | None:
    """キューブの風の区分(10種類): 'none'、または '{tail|head|cross}_{weak|mid|strong}'。

    無風(風向17)または風速0は 'none' にまとめる。どちらかが欠損なら None。
    """
    if direction is None or speed is None:
        return None
    wc = wind_class(direction)
    if wc == "none" or speed == 0:
        return "none"
    return f"{wc}_{wind_speed_band(speed)}"


WIND_BUCKETS = ("none",) + tuple(f"{c}_{s}" for c in ("tail", "head", "cross") for s in WIND_SPEED_BANDS)

# グレードの3区分(grade_number → 区分)。SG(1)・PG1(6)・G1(2) は上位
GRADE_BUCKETS = ("top", "g2g3", "general")
_GRADE_TO_BUCKET = {1: "top", 6: "top", 2: "top", 3: "g2g3", 4: "g2g3", 5: "general"}


def grade_bucket(grade_number: int | None) -> str | None:
    return _GRADE_TO_BUCKET.get(grade_number) if grade_number is not None else None


# 本命の確率帯(モデルの弱点分析: 10%刻み。週のまとめ: 3区分)
def prob_band10(p: float) -> str:
    """'0-10' … '90-100'。"""
    if not 0 <= p <= 1:
        raise ValueError(f"確率は0〜1で指定してください: {p}")
    i = min(int(p * 10), 9)
    return f"{i * 10}-{i * 10 + 10}"


PROB_BANDS3 = ("lt35", "35to50", "ge50")


def prob_band3(p: float) -> str:
    """〜35%、35〜50%、50%〜(堅さの区分 05 3章と同じ境界)。"""
    if p < 0.35:
        return "lt35"
    if p < 0.50:
        return "35to50"
    return "ge50"


MANSHU_YEN = 10_000  # 万舟: 3連単の払戻が10,000円以上


def is_manshu(trifecta_amount: int | None) -> bool | None:
    return None if trifecta_amount is None else trifecta_amount >= MANSHU_YEN


# ---------------------------------------------------------------------------
# シミュレーター(spec/11 2.6、Q16)
# ---------------------------------------------------------------------------

SIM_BUDGET_PER_RACE_YEN = 1_000

# ---------------------------------------------------------------------------
# 公開ファイルのサイズ上限(gzip後のバイト数。spec/11 1.2)
# ---------------------------------------------------------------------------

KB = 1024
SIZE_LIMITS_GZIP: dict[str, int] = {
    "index": 5 * KB,
    "stadiums": 100 * KB,
    "cube_per_stadium": 60 * KB,
    "cube_total": 1024 * KB,
    "rankings": 50 * KB,
    "model_weakness": 80 * KB,
    "simulator": 150 * KB,
    "weekly": 20 * KB,
    "personas": 30 * KB,  # 仮想の購入者(spec/11 8章)
}


class SizeLimitExceeded(ValueError):
    pass


def check_size(kind: str, gzip_bytes: int) -> None:
    """上限を超えていれば SizeLimitExceeded(黙って大きなファイルを出さない)。"""
    if kind not in SIZE_LIMITS_GZIP:
        raise KeyError(f"未知のファイルの種類です: {kind}")
    limit = SIZE_LIMITS_GZIP[kind]
    if gzip_bytes > limit:
        raise SizeLimitExceeded(f"{kind}: gzip後 {gzip_bytes} バイトが上限 {limit} バイトを超えています")
