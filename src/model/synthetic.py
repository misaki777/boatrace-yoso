"""テスト・動作確認用の合成データ(spec/02 の列仕様に合わせた架空の特徴量テーブル)。

実データ(data/features/)が出来るまでの間、学習・評価・バックテストを一通り動かすために使う。
値は架空で、実際のボートレースの傾向を正確に再現するものではない。

CLI(動作確認用。出力先は任意の場所を指定する):
    python -m src.model.synthetic --out-dir /path/to/dir [--races-per-day 8]
    → features_pre.parquet, payouts.parquet, odds.parquet を出す
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.model import combinations as cb
from src.model.data import _FALLBACK_PRE, _FALLBACK_PREVIEW_ONLY

# 枠番ごとの強さ(インが強い)
_COURSE_EFFECT = np.array([1.7, 0.55, 0.45, 0.25, 0.0, -0.35])
_RACE_KINDS = ["yosen", "jun_yusho", "yusho", "tokusen", "ippan", "other"]
_WIND_CLASSES = ["tail", "head", "none", "cross"]


def make_synthetic_features(
    date_from: str = "2026-01-01",
    date_to: str = "2026-10-31",
    races_per_day: int = 8,
    seed: int = 0,
    p_canceled: float = 0.01,
    p_absent: float = 0.02,
    p_dead_heat: float = 0.005,
) -> pd.DataFrame:
    """合成の特徴量テーブル(1行=1艇)を作る。pre と preview の両方の列を含む。

    含める状態: 中止レース(is_valid_result=False)、欠場艇(is_absent=True)、1着の同着。
    隠れ列 `_true_strength`(生成に使った強さ)も残す(オッズを作るのに使う。特徴量ではない)。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(date_from, date_to, freq="D")
    n_races = len(dates) * races_per_day
    n_rows = n_races * 6

    # 選手のプール(登録番号と隠れた実力)
    n_racers = 400
    racer_numbers = rng.choice(np.arange(3000, 5400), size=n_racers, replace=False)
    racer_skill = rng.normal(0, 1, n_racers)

    if races_per_day > 24 * 12:
        raise ValueError("races_per_day は288以下にしてください")
    entry = np.tile(np.arange(1, 7), n_races)
    date = np.repeat(np.repeat(dates.values, races_per_day), 6)
    # 1日の中で (場, R) が重ならないように選ぶ
    slots = np.concatenate([np.sort(rng.choice(24 * 12, races_per_day, replace=False)) for _ in dates])
    stadium = np.repeat(slots // 12 + 1, 6)
    race_number = np.repeat(slots % 12 + 1, 6)

    # レースごとに6人を重複なしで選ぶ
    racer_idx = np.concatenate([rng.choice(n_racers, 6, replace=False) for _ in range(n_races)])
    skill = racer_skill[racer_idx]
    motor = rng.normal(0, 1, n_rows)

    strength = _COURSE_EFFECT[entry - 1] + 0.8 * skill + 0.3 * motor
    gumbel = rng.gumbel(0, 1, n_rows)
    noisy = strength + gumbel

    race_ids = [
        f"{pd.Timestamp(d):%Y%m%d}-{s:02d}-{r:02d}" for d, s, r in zip(date[::6], stadium[::6], race_number[::6])
    ]
    df = pd.DataFrame({"race_id": np.repeat(race_ids, 6)})
    df["date"] = pd.to_datetime(date)
    df["stadium_number"] = stadium.astype("int8")
    df["race_number"] = race_number.astype("int8")
    df["entry_number"] = entry.astype("int8")
    df["racer_number"] = racer_numbers[racer_idx]

    # --- 状態(中止・欠場・同着)と着順 ---
    canceled = np.repeat(rng.random(n_races) < p_canceled, 6)
    absent_race = rng.random(n_races) < p_absent
    absent = np.zeros(n_rows, dtype=bool)
    absent_pos = np.where(absent_race)[0] * 6 + rng.integers(0, 6, absent_race.sum())
    absent[absent_pos] = True
    absent &= ~canceled

    order_key = np.where(absent, -np.inf, noisy).reshape(n_races, 6)
    ranks = (-order_key).argsort(axis=1).argsort(axis=1) + 1  # 1が1着
    finish = ranks.astype(float).reshape(-1)
    finish[absent] = np.nan
    # 同着1着: 2着の艇も1着にする
    dead_heat = (rng.random(n_races) < p_dead_heat) & ~absent_race
    dh_rows = np.repeat(dead_heat, 6) & (finish == 2)
    finish[dh_rows] = 1
    finish[canceled] = np.nan

    df["finish_position"] = pd.array(np.where(np.isnan(finish), pd.NA, finish), dtype="Int8")
    fp = df["finish_position"]
    df["is_win"] = (fp == 1).astype("Int8").where(fp.notna(), pd.NA)
    df["is_top2"] = (fp <= 2).astype("Int8").where(fp.notna(), pd.NA)
    df["is_top3"] = (fp <= 3).astype("Int8").where(fp.notna(), pd.NA)
    df.loc[absent, ["is_win", "is_top2", "is_top3"]] = 0
    df["is_valid_result"] = ~canceled
    df["is_absent"] = absent

    # --- P: 出走表 ---
    df["grade_number"] = np.repeat(rng.choice([1, 2, 3, 4, 5, 6], n_races, p=[0.6, 0.15, 0.1, 0.08, 0.04, 0.03]), 6)
    df["day_number"] = np.repeat(rng.integers(1, 7, n_races), 6)
    df["race_kind"] = np.repeat(rng.choice(_RACE_KINDS, n_races), 6)
    nat_wr = np.clip(5.3 + 1.2 * skill + rng.normal(0, 0.4, n_rows), 1.0, 9.0)
    df["rank_number"] = np.select([nat_wr >= 6.5, nat_wr >= 5.5, nat_wr >= 4.0], [1, 2, 3], 4)
    df["age"] = rng.integers(20, 60, n_rows)
    df["weight"] = rng.normal(53, 3, n_rows).round(1)
    df["flying_count"] = rng.choice([0, 0, 0, 0, 1, 2], n_rows)
    df["late_count"] = rng.choice([0, 0, 0, 1], n_rows)
    df["average_start_timing"] = np.clip(0.17 - 0.01 * skill + rng.normal(0, 0.02, n_rows), 0.08, 0.30).round(2)
    df["national_win_rate"] = nat_wr.round(2)
    df["national_top_2_percent"] = np.clip(30 + 12 * skill + rng.normal(0, 5, n_rows), 0, 100).round(2)
    df["national_top_3_percent"] = np.clip(45 + 12 * skill + rng.normal(0, 5, n_rows), 0, 100).round(2)
    local_missing = rng.random(n_rows) < 0.1
    df["local_win_rate"] = np.where(local_missing, 0.0, np.clip(nat_wr + rng.normal(0, 0.8, n_rows), 0, 10)).round(2)
    df["local_top_2_percent"] = np.where(local_missing, 0.0, df["national_top_2_percent"] + rng.normal(0, 6, n_rows))
    df["local_top_3_percent"] = np.where(local_missing, 0.0, df["national_top_3_percent"] + rng.normal(0, 6, n_rows))
    df["local_win_rate_missing"] = local_missing
    df["motor_top_2_percent"] = np.clip(35 + 8 * motor + rng.normal(0, 4, n_rows), 0, 100).round(2)
    df["motor_top_3_percent"] = np.clip(50 + 8 * motor + rng.normal(0, 4, n_rows), 0, 100).round(2)
    df["boat_top_2_percent"] = np.clip(35 + rng.normal(0, 8, n_rows), 0, 100).round(2)
    df["boat_top_3_percent"] = np.clip(50 + rng.normal(0, 8, n_rows), 0, 100).round(2)
    df["is_home_branch"] = rng.random(n_rows) < 0.15

    # --- R: レース内の相対値 ---
    g = df.groupby("race_id", sort=False)
    df["r_win_rate_rank"] = g["national_win_rate"].rank(ascending=False, method="min").astype(int)
    # 行はレースごとに6行ずつ連続しているので、(レース数, 6) に並べ替えて計算する
    wr = df["national_win_rate"].to_numpy().reshape(n_races, 6)
    others_max = np.stack([np.delete(wr, k, axis=1).max(axis=1) for k in range(6)], axis=1)
    df["r_win_rate_diff_max"] = (wr - others_max).reshape(-1)
    df["r_win_rate_diff_mean"] = df["national_win_rate"] - g["national_win_rate"].transform("mean")
    df["r_st_rank"] = g["average_start_timing"].rank(method="min").astype(int)
    df["r_motor2_rank"] = g["motor_top_2_percent"].rank(ascending=False, method="min").astype(int)
    inner = np.full((n_races, 6), np.nan)
    for k in range(1, 6):
        inner[:, k] = wr[:, :k].max(axis=1)
    df["r_inner_strength"] = inner.reshape(-1)
    a1 = (df["rank_number"].to_numpy() == 1).reshape(n_races, 6).sum(axis=1)
    df["r_a1_count"] = np.repeat(a1, 6)

    # --- H: 過去の集計(架空。実力と相関させる) ---
    df["h_racer_n_90d"] = rng.integers(0, 60, n_rows)
    early = df["h_racer_n_90d"].to_numpy() == 0
    df["h_racer_win_rate_90d"] = np.where(early, np.nan, np.clip(0.17 + 0.08 * skill + rng.normal(0, 0.05, n_rows), 0, 1))
    df["h_racer_top2_90d"] = np.where(early, np.nan, np.clip(0.33 + 0.1 * skill + rng.normal(0, 0.06, n_rows), 0, 1))
    df["h_racer_top3_90d"] = np.where(early, np.nan, np.clip(0.5 + 0.1 * skill + rng.normal(0, 0.06, n_rows), 0, 1))
    df["h_racer_avg_finish_90d"] = np.where(early, np.nan, np.clip(3.5 - 0.6 * skill + rng.normal(0, 0.3, n_rows), 1, 7))
    df["h_racer_course_win_rate"] = np.clip(0.17 + 0.1 * (_COURSE_EFFECT[entry - 1]) + 0.04 * skill, 0, 1)
    df["h_racer_real_st_90d"] = df["average_start_timing"] + rng.normal(0, 0.01, n_rows)
    df["h_racer_course_change_rate"] = np.clip(rng.beta(1, 15, n_rows), 0, 1)
    df["h_stadium_course_win_rate_365d"] = np.clip(0.17 + 0.12 * _COURSE_EFFECT[entry - 1] + rng.normal(0, 0.02, n_rows), 0, 1)
    df["h_motor_series_top2"] = np.clip(0.35 + 0.1 * motor + rng.normal(0, 0.15, n_rows), 0, 1)
    df["h_racer_series_avg_finish"] = np.clip(3.5 - 0.4 * skill + rng.normal(0, 0.8, n_rows), 1, 7)
    df["h_days_since_last_race"] = rng.integers(1, 40, n_rows).astype(float)

    # --- V: 直前情報 ---
    df["pv_exhibition_time"] = (6.8 - 0.03 * motor + rng.normal(0, 0.05, n_rows)).round(2)
    g = df.groupby("race_id", sort=False)
    df["r_pv_exhibition_rank"] = g["pv_exhibition_time"].rank(method="min").astype(int)
    df["r_pv_exhibition_diff_best"] = df["pv_exhibition_time"] - g["pv_exhibition_time"].transform("min")
    df["pv_start_timing"] = np.clip(df["average_start_timing"] + rng.normal(0, 0.03, n_rows), -0.05, 0.4).round(2)
    df["pv_course_number"] = entry
    df["pv_course_changed"] = False
    df["pv_tilt_adjustment"] = rng.choice([-0.5, 0.0, 0.5], n_rows)
    df["pv_weight_adjustment"] = rng.choice([0.0, 0.0, 0.5, 1.0], n_rows)
    df["pv_propeller_new"] = rng.random(n_rows) < 0.05
    df["pv_parts_count"] = rng.choice([0, 0, 0, 1, 2], n_rows)
    df["pv_wind_speed"] = np.repeat(rng.integers(0, 8, n_races), 6)
    df["pv_wave_height"] = np.repeat(rng.integers(0, 10, n_races), 6)
    df["pv_air_temperature"] = np.repeat(rng.normal(18, 6, n_races).round(1), 6)
    df["pv_water_temperature"] = np.repeat(rng.normal(19, 5, n_races).round(1), 6)
    df["pv_wind_class"] = np.repeat(rng.choice(_WIND_CLASSES, n_races), 6)
    df["pv_weather_number"] = np.repeat(rng.choice([1, 2, 3], n_races), 6)

    df["_true_strength"] = strength

    missing = [c for c in _FALLBACK_PRE + _FALLBACK_PREVIEW_ONLY if c not in df.columns]
    assert not missing, missing
    return df


def make_synthetic_market(features: pd.DataFrame, seed: int = 0, takeout: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """合成の payouts と odds(interim と同じ列)を作る。単勝・2連単・3連単のみ。

    市場の確率は「本当の強さ + ノイズ」から Harville で作り、オッズ = (1 - 控除率) / 市場確率 とする。
    払戻 = 的中した組合せのオッズ × 100。
    """
    rng = np.random.default_rng(seed + 1)
    df = features[features["is_valid_result"]]
    codes, s = _race_matrix_strength(df)
    s = s + rng.normal(0, 0.3, s.shape)
    s = np.nan_to_num(s, nan=-50.0)
    p_market = np.exp(s - s.max(axis=1, keepdims=True))
    p_market /= p_market.sum(axis=1, keepdims=True)

    odds_frames = []
    for bt in ("win", "exacta", "trifecta"):
        probs = cb.combination_prob_matrix(p_market, bt)
        odds = np.maximum(np.floor((1 - takeout) / np.maximum(probs, 1e-6) * 10) / 10, 1.0)
        labels = cb.combination_labels(bt)
        odds_frames.append(
            pd.DataFrame(
                {
                    "race_id": np.repeat(codes, len(labels)),
                    "bet_type": bt,
                    "combination": np.tile(labels, len(codes)),
                    "odds": odds.reshape(-1).astype("float32"),
                    "odds_lower": np.float32(np.nan),
                    "odds_upper": np.float32(np.nan),
                }
            )
        )
    odds_df = pd.concat(odds_frames, ignore_index=True)

    # 払戻: 実際の着順(同着1着なら両方の並びに払戻がある)
    lookup = odds_df.set_index(["race_id", "bet_type", "combination"])["odds"]
    rows = []
    for race_id, g in df.groupby("race_id", sort=False):
        fp = g.set_index("entry_number")["finish_position"].dropna().astype(int)
        firsts = sorted(fp[fp == 1].index)
        rest = fp[fp > 1].sort_values()
        orders = []
        if len(firsts) == 1:
            orders.append([firsts[0]] + list(rest.index[:2]))
        else:
            # 同着1着: どちらが先でも的中
            for a in firsts:
                others = [b for b in firsts if b != a]
                orders.append([a] + others + list(rest.index[: 3 - len(firsts)]))
        seen = set()
        for order in orders:
            for bt, combo in (("win", f"{order[0]}"), ("exacta", f"{order[0]}-{order[1]}"),
                              ("trifecta", f"{order[0]}-{order[1]}-{order[2]}")):
                if (bt, combo) in seen:
                    continue
                seen.add((bt, combo))
                amount = int(round(float(lookup[(race_id, bt, combo)]) * 100 / 10) * 10)
                rows.append((race_id, bt, combo, amount, None))
    payouts = pd.DataFrame(rows, columns=["race_id", "bet_type", "combination", "amount", "label"])
    return payouts, odds_df


def _race_matrix_strength(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    from src.model.data import race_matrix

    codes, s = race_matrix(df, "_true_strength")
    absent_codes, absent = race_matrix(df.assign(_a=df["is_absent"].astype(float)), "_a", fill=0.0)
    s[absent == 1] = np.nan  # 欠場艇は市場でも買えない扱い(オッズは極端に高くなる)
    return codes, s


def main() -> None:
    parser = argparse.ArgumentParser(description="合成データを書き出す(動作確認用)")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--from", dest="date_from", default="2026-01-01")
    parser.add_argument("--to", dest="date_to", default="2026-10-31")
    parser.add_argument("--races-per-day", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    feats = make_synthetic_features(args.date_from, args.date_to, args.races_per_day, args.seed)
    payouts, odds = make_synthetic_market(feats, args.seed)
    feats.to_parquet(out / "features_pre.parquet", index=False)
    payouts.to_parquet(out / "payouts.parquet", index=False)
    odds.to_parquet(out / "odds.parquet", index=False)
    print(f"features: {len(feats)} 行, payouts: {len(payouts)} 行, odds: {len(odds)} 行 → {out}")


if __name__ == "__main__":
    main()
