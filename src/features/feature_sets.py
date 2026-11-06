"""特徴量の列リスト(特徴量担当 → モデル担当の契約。02_features.md 2章)。

モデル担当はここを import して使う(列名を手で書き写さない)。変えるときはPMを通す。

- 特徴量の列はすべて数値(float32。entry_number / stadium_number / race_number は int8)。
  欠損は NaN。LightGBM にそのまま渡せる。
- カテゴリ列も整数コードの数値で入っている。LightGBM では
  `categorical_feature=categorical_features(variant)` を指定する。
- race_kind と pv_wind_class は文字列ではなく、下の *_CODES の整数コードで入っている
  (学習時と予想時でコードがずれないよう、対応を固定するため)。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# キー・目的変数・メタ列(特徴量ではない。2.1節)
# ---------------------------------------------------------------------------

KEY_COLUMNS: list[str] = [
    "race_id", "date", "stadium_number", "race_number", "entry_number", "racer_number",
]
# 目的変数。当日用(--date)ではすべて null
TARGET_COLUMNS: list[str] = ["finish_position", "is_win", "is_top2", "is_top3"]
# 学習データの絞り込みに使う列。学習では is_valid_result=True かつ is_absent=False の行を使う
META_COLUMNS: list[str] = ["is_valid_result", "is_absent"]

# ---------------------------------------------------------------------------
# カテゴリのコード表(固定。順番を変えないこと。追加は末尾に)
# ---------------------------------------------------------------------------

RACE_KIND_CODES: dict[str, int] = {
    "ippan": 0,      # 一般
    "yosen": 1,      # 予選
    "tokusen": 2,    # 特選・特賞・選抜・ドリーム
    "jun_yusho": 3,  # 準優勝戦
    "yusho": 4,      # 優勝戦
    "other": 5,      # 上のどれにも当たらない(場独自の愛称など)
}

WIND_CLASS_CODES: dict[str, int] = {"none": 0, "tail": 1, "head": 2, "cross": 3}

# ---------------------------------------------------------------------------
# 特徴量(区分 P・R・H・V)
# ---------------------------------------------------------------------------

# P: 出走表(前日に確定)
P_FEATURES: list[str] = [
    "entry_number",
    "stadium_number",
    "race_number",
    "grade_number",
    "day_number",
    "race_kind",
    "rank_number",
    "age",
    "weight",
    "flying_count",
    "late_count",
    "average_start_timing",
    "national_win_rate",
    "national_top_2_percent",
    "national_top_3_percent",
    "local_win_rate",
    "local_top_2_percent",
    "local_top_3_percent",
    "local_win_rate_missing",
    "motor_top_2_percent",
    "motor_top_3_percent",
    "boat_top_2_percent",
    "boat_top_3_percent",
    "is_home_branch",
]

# R: レース内の相対値(出走表から作るもの)
R_FEATURES: list[str] = [
    "r_win_rate_rank",
    "r_win_rate_diff_max",
    "r_win_rate_diff_mean",
    "r_st_rank",
    "r_motor2_rank",
    "r_inner_strength",
    "r_a1_count",
]

# H: 過去の結果の集計(対象日より前の結果だけで作る)
H_FEATURES: list[str] = [
    "h_racer_n_90d",
    "h_racer_win_rate_90d",
    "h_racer_top2_90d",
    "h_racer_top3_90d",
    "h_racer_avg_finish_90d",
    "h_racer_course_win_rate",
    "h_racer_real_st_90d",
    "h_racer_course_change_rate",
    "h_stadium_course_win_rate_365d",
    "h_motor_series_top2",
    "h_racer_series_avg_finish",
    "h_days_since_last_race",
]

# V: 直前情報(variant=preview のときだけ)。R との組合せ(V+R)もここに入れる
V_FEATURES: list[str] = [
    "pv_exhibition_time",
    "r_pv_exhibition_rank",
    "r_pv_exhibition_diff_best",
    "pv_start_timing",
    "pv_course_number",
    "pv_course_changed",
    "pv_tilt_adjustment",
    "pv_weight_adjustment",
    "pv_propeller_new",
    "pv_parts_count",
    "pv_wind_speed",
    "pv_wave_height",
    "pv_air_temperature",
    "pv_water_temperature",
    "pv_wind_class",
    "pv_weather_number",
]

PRE_FEATURES: list[str] = P_FEATURES + R_FEATURES + H_FEATURES
PREVIEW_FEATURES: list[str] = PRE_FEATURES + V_FEATURES

# カテゴリ扱いにする列(variant に含まれるものだけを使うこと。categorical_features() を参照)
# rank_number は順序があるので数値のまま扱う(仕様で可)
CATEGORICAL_FEATURES: list[str] = [
    "stadium_number",
    "grade_number",
    "race_kind",
    "pv_wind_class",
    "pv_weather_number",
]

# --- レース運びの特徴量(v2。2026-11-05 追加。既存の列・コード表は変えない) ---
# H: 選手×進入コースの決まり手・着の取り方・ST、選手の F・L・前付け。累計(_all)と直近180日(_180d)。
#    率は全体平均への縮小 (k + 10・p) / (n + 10) 済み。h_cs_n_* は件数
# R: 予定1コースの逃げ率と、2コースの差し率・3〜4コースのまくり系の率との差、内側の艇とのST差
# 予定コースは pre_v2 では枠番、preview_v2 では pv_course_number(無ければ枠番)
STYLE_H_FEATURES: list[str] = [
    f"h_cs_n_{w}" if v == "n" else f"h_cs_{v}_{w}" if not v.startswith("racer_") else f"h_{v}_{w}"
    for w in ("all", "180d")
    for v in ("n", "nige", "sashi", "makuri", "makurisashi", "otherwin", "second", "third", "low",
              "st_mean", "st_std", "racer_f_rate", "racer_late_rate", "racer_maeduke_rate")
]
STYLE_R_FEATURES: list[str] = [
    "r_sty_in_nige",
    "r_sty_nige_minus_sashi2",
    "r_sty_nige_minus_makuri34",
    "r_sty_st_diff_inner",
]
PRE_V2_FEATURES: list[str] = PRE_FEATURES + STYLE_H_FEATURES + STYLE_R_FEATURES
PREVIEW_V2_FEATURES: list[str] = PREVIEW_FEATURES + STYLE_H_FEATURES + STYLE_R_FEATURES

# --- 展示の相対値(v3。2026-11-06 追加。直前版だけ) ---
# 予定コース(展示の進入、無ければ枠番)で外側(4〜6)・内側(1〜3)に分けた展示ST・展示タイムの順位など。
# 展示ST の F(負)は、順位では最速、差では 0.00 として扱い、pv_st_flying に F かどうかを持つ
EX_FEATURES: list[str] = [
    "r_pv_st_rank",
    "pv_st_flying",
    "r_pv_st_rank_group",
    "r_pv_st_diff_best",
    "r_pv_st_diff_inner",
    "r_pv_exhibition_rank_group",
    "pv_outer_both_top",
]
PREVIEW_V3_FEATURES: list[str] = PREVIEW_V2_FEATURES + EX_FEATURES

VARIANTS: tuple[str, ...] = ("pre", "preview", "pre_v2", "preview_v2", "preview_v3")

# 特徴量の列名に含まれてはいけない文字列(リーク防止。02_features.md 3章2項)
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = ("rs_", "place", "odds", "payout", "technique")


def features_for(variant: str) -> list[str]:
    """variant の特徴量の列リストを返す。"""
    if variant == "pre":
        return list(PRE_FEATURES)
    if variant == "preview":
        return list(PREVIEW_FEATURES)
    if variant == "pre_v2":
        return list(PRE_V2_FEATURES)
    if variant == "preview_v2":
        return list(PREVIEW_V2_FEATURES)
    if variant == "preview_v3":
        return list(PREVIEW_V3_FEATURES)
    raise ValueError(f"未知の variant です: {variant}")


def categorical_features(variant: str) -> list[str]:
    """variant に含まれるカテゴリ列だけを返す。"""
    cols = set(features_for(variant))
    return [c for c in CATEGORICAL_FEATURES if c in cols]


# ---------------------------------------------------------------------------
# 展開予想(決まり手の確率)の特徴量。1行=1レース(F-9。spec/10 3.1節)
# 予定コース順に並べ直した各艇の値は c1_〜c6_ の接頭辞付き(c1 = 予定1コースの艇)
# ---------------------------------------------------------------------------

TENKAI_VARIANTS: tuple[str, ...] = ("pre", "preview", "preview_v3")
TENKAI_KEY_COLUMNS: list[str] = ["race_id", "date", "stadium_number", "race_number"]
# 目的変数 technique_number(1 逃げ 2 差し 3 まくり 4 まくり差し 5 抜き 6 恵まれ)。当日用は null
TENKAI_TARGET_COLUMNS: list[str] = ["technique_number", "is_valid_result"]

_TENKAI_BOAT_PRE: list[str] = [
    "entry_number", "national_win_rate", "rank_number", "average_start_timing",
    "motor_top_2_percent",
    "h_cs_n_all", "h_cs_nige_all", "h_cs_sashi_all", "h_cs_makuri_all", "h_cs_makurisashi_all",
    "h_cs_st_mean_all", "h_racer_f_rate_all", "h_racer_maeduke_rate_all",
    "r_sty_st_diff_inner",
]
_TENKAI_BOAT_PREVIEW_EXTRA: list[str] = ["pv_exhibition_time", "r_pv_exhibition_rank", "pv_start_timing"]
_TENKAI_RACE_PRE: list[str] = [
    "stadium_number", "grade_number", "race_kind",
    "r_sty_in_nige", "r_sty_nige_minus_sashi2", "r_sty_nige_minus_makuri34",
]
_TENKAI_RACE_PREVIEW_EXTRA: list[str] = ["pv_wind_class", "pv_wind_speed", "pv_wave_height"]


def tenkai_boat_columns(variant: str) -> list[str]:
    """艇ごとの列(c1_〜c6_ を付ける前の名前)。"""
    if variant == "pre":
        return list(_TENKAI_BOAT_PRE)
    if variant == "preview":
        return _TENKAI_BOAT_PRE + _TENKAI_BOAT_PREVIEW_EXTRA
    if variant == "preview_v3":  # 展開予想の直前版 + 展示の相対値(v3)
        return _TENKAI_BOAT_PRE + _TENKAI_BOAT_PREVIEW_EXTRA + EX_FEATURES
    raise ValueError(f"未知の variant です: {variant}")


def tenkai_race_columns(variant: str) -> list[str]:
    if variant == "pre":
        return list(_TENKAI_RACE_PRE)
    if variant in ("preview", "preview_v3"):
        return _TENKAI_RACE_PRE + _TENKAI_RACE_PREVIEW_EXTRA
    raise ValueError(f"未知の variant です: {variant}")


# 定義上いつも欠損になる列(1コースには内側の艇がいない)
_TENKAI_ALWAYS_MISSING = {(1, "r_pv_st_diff_inner")}


def tenkai_features_for(variant: str) -> list[str]:
    """展開予想モデルの特徴量の列リスト。"""
    boat = [f"c{c}_{name}" for name in tenkai_boat_columns(variant) for c in range(1, 7)
            if (c, name) not in _TENKAI_ALWAYS_MISSING]
    return tenkai_race_columns(variant) + boat


def tenkai_categorical_features(variant: str) -> list[str]:
    cats = ["stadium_number", "grade_number", "race_kind"]
    if variant.startswith("preview"):
        cats.append("pv_wind_class")
    return cats
