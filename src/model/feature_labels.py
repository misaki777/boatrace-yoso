"""特徴量の表示用ラベル(確率の根拠トップ3。spec/08 5.2)。

- label_for(name): 日本語ラベル。対応が無ければ名前のまま返す。
- is_value_public(name): 特徴量の「値」を公開JSONに出してよいか(spec/04 1章。年齢・体重・
  チルト・部品交換などの詳細は出さない)。ラベル(要因の名前と向き)は出してよい。
- コード値(場・グレード・レース種別・風向・天候)の値は、数字のままでは意味が無いので値は出さない。
"""

from __future__ import annotations

FEATURE_LABELS: dict[str, str] = {
    # P: 出走表
    "entry_number": "枠番",
    "stadium_number": "場",
    "race_number": "レース番号",
    "grade_number": "グレード",
    "day_number": "開催日目",
    "race_kind": "レースの種類(予選・優勝戦など)",
    "rank_number": "級別",
    "age": "年齢",
    "weight": "体重",
    "flying_count": "フライング回数",
    "late_count": "出遅れ回数",
    "average_start_timing": "平均スタートタイミング",
    "national_win_rate": "全国勝率",
    "national_top_2_percent": "全国2連対率",
    "national_top_3_percent": "全国3連対率",
    "local_win_rate": "当地勝率",
    "local_top_2_percent": "当地2連対率",
    "local_top_3_percent": "当地3連対率",
    "local_win_rate_missing": "当地の出走実績なし",
    "motor_top_2_percent": "モーター2連対率",
    "motor_top_3_percent": "モーター3連対率",
    "boat_top_2_percent": "ボート2連対率",
    "boat_top_3_percent": "ボート3連対率",
    "is_home_branch": "地元選手",
    # R: レース内の相対値
    "r_win_rate_rank": "全国勝率のレース内順位",
    "r_win_rate_diff_max": "全国勝率(他艇の最高との差)",
    "r_win_rate_diff_mean": "全国勝率(レース平均との差)",
    "r_st_rank": "平均STのレース内順位",
    "r_motor2_rank": "モーター2連対率のレース内順位",
    "r_inner_strength": "内側の艇の強さ",
    "r_a1_count": "レース内のA1選手の数",
    # H: 過去の集計
    "h_racer_n_90d": "直近90日の出走数",
    "h_racer_win_rate_90d": "直近90日の1着率",
    "h_racer_top2_90d": "直近90日の2連対率",
    "h_racer_top3_90d": "直近90日の3連対率",
    "h_racer_avg_finish_90d": "直近90日の平均着順",
    "h_racer_course_win_rate": "このコースでの1着率",
    "h_racer_real_st_90d": "直近90日の本番ST",
    "h_racer_course_change_rate": "前付けの傾向",
    "h_stadium_course_win_rate_365d": "この場・このコースの1着率(1年)",
    "h_motor_series_top2": "今節のモーター2連対率",
    "h_racer_series_avg_finish": "今節の平均着順",
    "h_days_since_last_race": "前走からの日数",
    # V: 直前情報
    "pv_exhibition_time": "展示タイム",
    "r_pv_exhibition_rank": "展示タイムのレース内順位",
    "r_pv_exhibition_diff_best": "展示タイム(最速との差)",
    "pv_start_timing": "スタート展示のST",
    "pv_course_number": "スタート展示の進入コース",
    "pv_course_changed": "スタート展示で枠なり以外の進入",
    "pv_tilt_adjustment": "チルト角",
    "pv_weight_adjustment": "体重調整",
    "pv_propeller_new": "新プロペラ",
    "pv_parts_count": "部品交換",
    "pv_wind_speed": "風速",
    "pv_wave_height": "波高",
    "pv_air_temperature": "気温",
    "pv_water_temperature": "水温",
    "pv_wind_class": "風向(追い風・向かい風など)",
    "pv_weather_number": "天候",
    # 選手のレース運び(pre_v2 / preview_v2。src/features/style.py)。cs = この選手 × 予定コース
    "h_cs_n_all": "このコースでの出走数(累計)",
    "h_cs_n_180d": "このコースでの出走数(半年)",
    "h_cs_nige_all": "このコースでの逃げの率(累計)",
    "h_cs_nige_180d": "このコースでの逃げの率(半年)",
    "h_cs_sashi_all": "このコースでの差しの率(累計)",
    "h_cs_sashi_180d": "このコースでの差しの率(半年)",
    "h_cs_makuri_all": "このコースでのまくりの率(累計)",
    "h_cs_makuri_180d": "このコースでのまくりの率(半年)",
    "h_cs_makurisashi_all": "このコースでのまくり差しの率(累計)",
    "h_cs_makurisashi_180d": "このコースでのまくり差しの率(半年)",
    "h_cs_otherwin_all": "このコースでのその他の決まり手の1着率(累計)",
    "h_cs_otherwin_180d": "このコースでのその他の決まり手の1着率(半年)",
    "h_cs_second_all": "このコースでの2着率(累計)",
    "h_cs_second_180d": "このコースでの2着率(半年)",
    "h_cs_third_all": "このコースでの3着率(累計)",
    "h_cs_third_180d": "このコースでの3着率(半年)",
    "h_cs_low_all": "このコースでの5・6着率(累計)",
    "h_cs_low_180d": "このコースでの5・6着率(半年)",
    "h_cs_st_mean_all": "このコースでの平均ST(累計)",
    "h_cs_st_mean_180d": "このコースでの平均ST(半年)",
    "h_cs_st_std_all": "このコースでのSTのばらつき(累計)",
    "h_cs_st_std_180d": "このコースでのSTのばらつき(半年)",
    "h_racer_f_rate_all": "フライング率(累計)",
    "h_racer_f_rate_180d": "フライング率(半年)",
    "h_racer_late_rate_all": "出遅れ率(累計)",
    "h_racer_late_rate_180d": "出遅れ率(半年)",
    "h_racer_maeduke_rate_all": "前付け率(累計)",
    "h_racer_maeduke_rate_180d": "前付け率(半年)",
    "r_sty_in_nige": "1号艇の逃げの率",
    "r_sty_nige_minus_sashi2": "1号艇の逃げと2号艇の差しの力関係",
    "r_sty_nige_minus_makuri34": "1号艇の逃げと3・4号艇のまくりの力関係",
    "r_sty_st_diff_inner": "内側の艇とのST差",
}

# 値を公開してはいけない特徴量(spec/04 1章)と、値に意味が無いコード値
_VALUE_NOT_PUBLIC = {
    "age", "weight", "pv_tilt_adjustment", "pv_weight_adjustment", "pv_propeller_new", "pv_parts_count",
    "stadium_number", "grade_number", "race_kind", "pv_wind_class", "pv_weather_number",
}


def label_for(name: str) -> str:
    """表示用ラベル。対応が無ければ名前のまま。"""
    return FEATURE_LABELS.get(name, name)


def is_value_public(name: str) -> bool:
    """特徴量の値を公開JSONに添えてよいか。"""
    return name not in _VALUE_NOT_PUBLIC


def unlabeled(features: list[str]) -> list[str]:
    """ラベルの無い特徴量の一覧。"""
    return [f for f in features if f not in FEATURE_LABELS]
