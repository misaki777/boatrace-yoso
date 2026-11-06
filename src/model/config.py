"""モデルまわりの設定(期間・パス・LightGBMのパラメータ)。spec/03 3章。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

# リポジトリのルート(src/model/config.py から2つ上)
ROOT = Path(__file__).resolve().parents[2]

FEATURES_DIR = ROOT / "data" / "features"
INTERIM_DIR = ROOT / "data" / "interim"
PREDICTIONS_DIR = ROOT / "data" / "predictions"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"

SEED = 42

# variant の一覧(feature_sets.VARIANTS と同じ)。model_id は "{variant}_{YYYYMMDD}_{hash4}"
VARIANTS: tuple[str, ...] = ("pre", "preview", "pre_v2", "preview_v2", "preview_v3")


def is_preview_variant(variant: str) -> bool:
    """直前情報を使う variant か(直前情報が欠けたレースは pre 系にフォールバックする)。"""
    return variant.startswith("preview")


def pre_variant_of(variant: str) -> str:
    """直前 variant に対応する pre 系の variant(preview → pre、preview_v2 → pre_v2)。"""
    return variant.replace("preview", "pre", 1)


def variant_of_model_id(model_id: str) -> str:
    """model_id の先頭から variant を取り出す(長い名前から照合する)。"""
    for v in sorted(VARIANTS, key=len, reverse=True):
        if model_id.startswith(v + "_") and model_id[len(v) + 1 : len(v) + 9].isdigit():
            return v
    return model_id.split("_", 1)[0]
N_BOATS = 6


@dataclass(frozen=True)
class Periods:
    """時系列分割の期間(両端を含む。日付は YYYY-MM-DD)。"""

    warmup_from: str = "2026-01-01"
    train_from: str = "2026-04-01"
    train_to: str = "2026-08-31"
    valid_from: str = "2026-09-01"
    valid_to: str = "2026-09-30"
    test_from: str = "2026-10-01"
    test_to: str = "2026-10-31"

    def range_of(self, name: str) -> tuple[str, str]:
        """'train' / 'valid' / 'test' の (from, to) を返す。"""
        if name not in ("train", "valid", "test"):
            raise ValueError(f"期間名が不正です: {name}")
        d = asdict(self)
        return d[f"{name}_from"], d[f"{name}_to"]

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


DEFAULT_PERIODS = Periods()

# LightGBM の初期パラメータ(spec/03 3章の目安)
LGBM_PARAMS: dict = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "learning_rate": 0.03,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
    "verbosity": -1,
    # スレッド数を指定しないと(全コア使用)、この環境(WSL)では極端に遅くなった
    # (50ラウンドで 0.08秒 → 38秒)。データが大きくなったら増やして試す
    "num_threads": 4,
}
NUM_BOOST_ROUND = 5000
EARLY_STOPPING_ROUNDS = 200

# 温度 T の探索範囲
TEMPERATURE_RANGE = (0.2, 5.0)

# バックテストのブートストラップ回数(spec/03 6章)
BOOTSTRAP_N = 1000
