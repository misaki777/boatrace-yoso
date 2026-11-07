"""LightGBM の model.txt を小さくする(日次ジョブで取得する容量を抑えるため)。

- leaf_value / internal_value / split_gain / leaf_weight / internal_weight の数値を有効数字 digits 桁に丸める。
  **分岐のしきい値(threshold)は丸めない**(丸めると分岐先が変わることがあるため)。
- tree_sizes(各木の文字数。丸めると合わなくなる)の行を消す。LightGBM はこの行が無いと順に読む。
- feature_importances の節(情報だけで予想には使わない)を消す。
丸めた後に、元のモデルと予想が一致すること(差が十分小さいこと)を compact_file の戻り値で確かめる。
"""

from __future__ import annotations

import re
from pathlib import Path

ROUND_KEYS = ("leaf_value=", "internal_value=", "split_gain=", "leaf_weight=", "internal_weight=")
_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _round_line(line: str, digits: int) -> str:
    key, _, vals = line.partition("=")
    return key + "=" + " ".join(f"{float(v):.{digits}g}" for v in vals.split(" ") if v != "")


def compact_text(text: str, digits: int = 6) -> str:
    out = []
    skip = False
    for line in text.split("\n"):
        if line.startswith("tree_sizes="):
            continue
        if line.startswith("feature_importances:"):
            skip = True
            continue
        if skip:
            if line.startswith("parameters:") or line.startswith("pandas_categorical"):
                skip = False
            else:
                continue
        if line.startswith(ROUND_KEYS):
            line = _round_line(line, digits)
        out.append(line)
    return "\n".join(out)


def compact_file(path: str | Path, X=None, digits: int = 6) -> dict:
    """path の model.txt を書き換える。X を渡すと、丸める前後の生スコアの最大の差を返す。"""
    from src.model.lgbm import import_lightgbm

    lgb = import_lightgbm()
    path = Path(path)
    before = path.stat().st_size
    text = path.read_text(encoding="utf-8")
    ref = lgb.Booster(model_str=text).predict(X, raw_score=True) if X is not None else None
    new = compact_text(text, digits)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(new, encoding="utf-8")
    out = {"before": before, "after": tmp.stat().st_size}
    if X is not None:
        got = lgb.Booster(model_file=str(tmp)).predict(X, raw_score=True)
        import numpy as np

        out["max_abs_diff_raw"] = float(np.max(np.abs(np.asarray(got) - np.asarray(ref))))
    tmp.replace(path)
    return out
