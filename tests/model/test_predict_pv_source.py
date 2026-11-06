"""直前版の確定ファイルに pv_source を記録する(M-18 用)。"""

import pandas as pd

from src.model.predict import attach_pv_source
from src.model.predict_tenkai import attach_pv_source as attach_tenkai


def test_attach_from_features_and_keep_existing():
    out = pd.DataFrame({"race_id": ["r1", "r1", "r2"], "entry_number": pd.array([1, 2, 1], dtype="int8")})
    feats = pd.DataFrame({"race_id": ["r1", "r1", "r2"], "entry_number": [1, 2, 1], "pv_source": ["official", "official", "openapi"]})
    a = attach_pv_source(out, feats, "2026-11-06")
    assert a["pv_source"].tolist() == ["official", "official", "openapi"]
    # 既に値がある行(確定済み)は変えない
    old = a.assign(pv_source=pd.Series(["none", None, None], dtype="string"))
    b = attach_pv_source(old, feats, "2026-11-06")
    assert b["pv_source"].tolist() == ["none", "official", "openapi"]


def test_attach_unknown_when_missing(monkeypatch):
    import src.features.build_interim as bi

    monkeypatch.setattr(bi, "read_interim", lambda *a, **k: pd.DataFrame(columns=["race_id", "entry_number"]))
    out = pd.DataFrame({"race_id": ["r1"], "entry_number": pd.array([1], dtype="int8")})
    a = attach_pv_source(out, pd.DataFrame({"race_id": ["r1"], "entry_number": [1]}), "2026-11-06")
    assert a["pv_source"].isna().all()


def test_tenkai_race_level():
    out = pd.DataFrame({"race_id": ["r1", "r2"]})
    feats = pd.DataFrame({"race_id": ["r1", "r2"], "pv_source": ["official", "none"]})
    assert attach_tenkai(out, feats, "2026-11-06")["pv_source"].tolist() == ["official", "none"]
