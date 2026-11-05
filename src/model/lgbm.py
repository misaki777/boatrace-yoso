"""LightGBM まわりの共通処理(学習・読み込み・スコア計算)。

lightgbm は OS に libgomp が無いと import できないため、このモジュールでは関数の中で import する
(lightgbm を使わない評価やテストが、libgomp の無い環境でも動くようにするため)。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.config import MODELS_DIR, variant_of_model_id


def import_lightgbm():
    """lightgbm を import して返す。libgomp が無い場合などは分かりやすいエラーにする。"""
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as e:  # libgomp.so.1 が無いと OSError になる
        raise RuntimeError(
            "lightgbm を import できません。OS に libgomp1 が無い可能性があります"
            "(sudo apt install libgomp1)。元のエラー: " + str(e)
        ) from e
    return lgb


def lightgbm_available() -> bool:
    try:
        import_lightgbm()
    except RuntimeError:
        return False
    return True


def prepare_X(df: pd.DataFrame, features: list[str], categorical: list[str]) -> pd.DataFrame:
    """モデルに渡す説明変数の表を作る。すべて float にする(bool は 0/1)。

    カテゴリ列は、特徴量担当の表では整数コードの数値で入っている(コード表は feature_sets.py で固定)。
    その場合は数値のまま渡し、Dataset の categorical_feature で指定する。
    文字列のカテゴリ列(合成データなど)だけは category 型にする。この場合の水準の対応は
    LightGBM がモデルファイルに保存し(pandas_categorical)、予測時に同じ対応で変換し直す。
    """
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise KeyError(f"特徴量の列が足りません: {missing}")
    X = df[features].copy()
    for col in features:
        if col in categorical and not pd.api.types.is_numeric_dtype(X[col]):
            X[col] = X[col].astype("string").astype("category")
        elif X[col].dtype == bool or str(X[col].dtype) == "boolean":
            X[col] = X[col].astype("Float64").astype(float)
        elif not pd.api.types.is_numeric_dtype(X[col]):
            raise TypeError(f"数値でもカテゴリでもない列があります: {col} ({X[col].dtype})")
        else:
            X[col] = pd.to_numeric(X[col]).astype(float)
    return X


def model_dir(model_id: str, models_dir: str | Path = MODELS_DIR) -> Path:
    """models/{variant}/{model_id}/。variant は model_id の先頭(pre_... / preview_...)。"""
    variant = variant_of_model_id(model_id)
    return Path(models_dir) / variant / model_id


def latest_path(variant: str = "pre", models_dir: str | Path = MODELS_DIR) -> Path:
    """採用モデルを指すファイル。pre は models/latest(export が読む)、preview は models/preview/latest。"""
    return Path(models_dir) / "latest" if variant == "pre" else Path(models_dir) / variant / "latest"


def resolve_model_id(model_id: str, models_dir: str | Path = MODELS_DIR, variant: str = "pre") -> str:
    """'latest' なら variant の latest ファイルに書かれた model_id を返す。"""
    if model_id != "latest":
        return model_id
    path = latest_path(variant, models_dir)
    if not path.exists():
        raise FileNotFoundError(f"{path} がありません(採用したモデルがまだ無い)")
    return path.read_text(encoding="utf-8").strip()


def load_model(model_id: str, models_dir: str | Path = MODELS_DIR, variant: str = "pre"):
    """(booster, meta) を返す。model_id='latest' のときは variant の latest を読む。"""
    lgb = import_lightgbm()
    model_id = resolve_model_id(model_id, models_dir, variant)
    d = model_dir(model_id, models_dir)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    booster = lgb.Booster(model_file=str(d / "model.txt"))
    return booster, meta


def harville_gamma_meta(gamma2: float = 1.0, gamma3: float = 1.0) -> dict | None:
    """meta.json の harville_gamma の値(spec/03 3章)。補正なし(両方1)なら None(JSON では null)。"""
    if gamma2 == 1.0 and gamma3 == 1.0:
        return None
    return {"gamma2": float(gamma2), "gamma3": float(gamma3)}


def gammas_from_meta(meta: dict) -> tuple[float, float]:
    """meta から (gamma2, gamma3) を読む。harville_gamma を正とし、無ければ gamma2/gamma3、それも無ければ1。"""
    if "harville_gamma" in meta:
        hg = meta["harville_gamma"]
        return (1.0, 1.0) if hg is None else (float(hg["gamma2"]), float(hg["gamma3"]))
    return float(meta.get("gamma2", 1.0)), float(meta.get("gamma3", 1.0))


def write_latest(model_id: str, variant: str, models_dir: str | Path = MODELS_DIR) -> Path:
    """latest ファイルを一時ファイル経由で置き換える。"""
    path = latest_path(variant, models_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(model_id + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def raw_scores(booster, df: pd.DataFrame, meta: dict) -> np.ndarray:
    """生のスコア(logit)を返す。"""
    X = prepare_X(df, meta["features"], meta["categorical"])
    return booster.predict(X, raw_score=True, num_iteration=meta.get("best_iteration"))
