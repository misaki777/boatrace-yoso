"""踏み込んだ展開予想: 目的変数の作り方と、展開の一言の型。"""

import numpy as np
import pandas as pd
import pytest

from src.model import tenkai2 as t2


def test_labels():
    assert t2.who_label(1, "nige") == "c1_nige" and t2.who_label(4, "makuri") == "c4_makuri"
    assert t2.who_label(2, "nuki") == "other" and t2.who_label(np.nan, "nige") is None
    lab = pd.Series(["c1_nige"] * 900 + ["c4_makuri"] * 90 + ["c6_sashi"] * 5 + ["other"] * 5)
    cls = t2.choose_classes(lab)
    assert cls == ["c1_nige", "c4_makuri", "other"]  # 少ない組合せは other に
    assert t2.encode(pd.Series(["c6_sashi", "c1_nige", None]), cls).tolist() == [2, 0, -1]


def test_start_labels():
    st = np.array([[0.15, 0.12, 0.18, 0.10, 0.20, 0.21],     # 4コースがトップ、4が3より速い
                   [0.10, 0.10, 0.18, 0.19, 0.20, 0.21],     # 1位が同タイム → 除外
                   [0.15, np.nan, 0.18, 0.10, 0.20, 0.21],   # 欠け → 除外
                   [-0.01, 0.12, 0.18, 0.18, 0.20, 0.21]])   # フライングは速いとみなす、3と4は同タイム
    assert t2.top_start_label(st).tolist() == [3, -1, -1, 0]
    assert t2.faster_label(st, 4).tolist() == [1, 0, 1, -1]
    assert t2.faster_label(st, 2).tolist() == [1, -1, -1, 0]  # 2行目は同タイム


def _row(**kw):
    r = {f"course{k}_entry": k for k in range(1, 7)}
    r.update({f"p_top_c{k}": 1 / 6 for k in range(1, 7)})
    r.update({"p_who_c1_nige": 0.55, "p_who_c4_makuri": 0.09, "p_who_c2_sashi": 0.08, "p_who_other": 0.28})
    r.update(kw)
    return r


def test_headline_format():
    cls = ["c1_nige", "c2_sashi", "c4_makuri", "other"]
    h = t2.headline(_row(p_top_c4=0.24), cls)
    assert h == "本線: 1号艇の逃げ(55%)。崩れるなら4号艇のまくり(9%)。カドの4号艇がトップスタートを取る確率 24%。"
    h2 = t2.headline(_row(p_top_c4=0.1, p_top_c2=0.3, course2_entry=3), cls)
    assert h2.endswith("トップスタートの候補は2コースの3号艇(30%)。")  # 直前版では予定コースと枠番が違う
    for w in ("最適", "必ず", "確実"):
        assert w not in h + h2


def test_headline_mixed():
    cls = ["c1_nige", "c3_makuri", "other"]
    h = t2.headline(_row(p_who_c1_nige=0.15, p_who_c3_makuri=0.13, p_who_c4_makuri=0.0, p_top_c3=0.23), cls)
    assert h.startswith("混戦。最も確率が高いのは1号艇の逃げ(15%)、次いで3号艇のまくり(13%)。")
    assert "本線" not in h
