"""M-13 毎月の作り直し: 期間・基準・承認制・切り替えのテスト(lightgbm を使わない)。"""

import pytest

from src.model import monthly as mo


def test_month_periods():
    sel, refit = mo.month_periods("2026-12", "2026-01-01")
    assert sel.train_from == "2026-04-01" and sel.train_to == "2026-10-31"
    assert (sel.valid_from, sel.valid_to) == ("2026-11-01", "2026-11-30")
    assert refit.train_to == "2026-11-30"


def test_ece():
    assert mo.ece([{"n": 10, "pred_mean": 0.5, "actual": 0.4}, {"n": 30, "pred_mean": 0.2, "actual": 0.2}]) == pytest.approx(0.025)
    assert mo.ece([]) is None


H_OK = {"ok": True}
C = {"win_logloss": 1.200, "trifecta_logloss": 3.80, "ece": 0.010}


def test_judge():
    L = {"win_logloss": 1.203, "trifecta_logloss": 3.81, "ece": 0.012}
    r, _ = mo.judge(C, L, {"candidate": 1.1, "locked": 1.2}, H_OK, True)
    assert all(r.values())
    r, why = mo.judge(C, {**L, "win_logloss": 1.201}, None, H_OK, True)  # 0.001 しか良くない
    assert r["a"] is False and r["d"] is True and any("判定できず" in w for w in why)
    r, _ = mo.judge(C, {**L, "trifecta_logloss": 3.79}, None, H_OK, True)
    assert r["b"] is False
    r, _ = mo.judge(C, {**L, "ece": 0.009}, None, H_OK, True)
    assert r["c"] is False
    r, _ = mo.judge(C, L, {"candidate": 1.25, "locked": 1.2}, H_OK, True)
    assert r["d"] is False
    r, _ = mo.judge(C, L, None, {"ok": False}, True)
    assert r["e"] is False
    r, _ = mo.judge({**C, "trifecta_logloss": None}, {**L, "trifecta_logloss": None}, None, H_OK, False)
    assert r["b"] is True  # 展開予想


def test_approval_period():
    assert mo.approval_required([], "2026-12")
    hist = [{"run_month": "2026-12"}, {"run_month": "2027-01"}]
    assert mo.approval_required(hist[:1], "2027-01")  # 1か月分の記録 → まだ承認制
    assert not mo.approval_required(hist, "2027-02")  # 2か月分の記録 → 3か月目から自動
    assert mo.approval_required(hist[:1] + [{"run_month": "2027-01"}], "2027-01")  # 同じ月の再実行は数えない


def _rec(slot="pre", cand="pre_20261202_aaaa", ok=True):
    return {"variant": slot, "candidate": cand, "all_criteria_met": ok, "adopted": False,
            "pending_approval": False, "reason": "", "_why": [], "run_month": "2026-12"}


def test_decide_and_approve(tmp_path):
    (tmp_path / "latest").write_text("pre_old\n")
    r = mo.decide(_rec(), approval=True, models_dir=tmp_path)
    assert r["pending_approval"] and not r["adopted"] and "_why" not in r
    assert (tmp_path / "latest").read_text().strip() == "pre_old"  # 承認制の期間は切り替えない
    mo.save_history(tmp_path, [r])
    h = mo.approve("pre_20261202_aaaa", tmp_path)
    assert h["adopted"] and not h["pending_approval"] and "approved_at" in h
    assert (tmp_path / "latest").read_text().strip() == "pre_20261202_aaaa"
    with pytest.raises(ValueError):
        mo.approve("pre_20261202_aaaa", tmp_path)  # 2回目は承認待ちではない
    with pytest.raises(KeyError):
        mo.approve("pre_x", tmp_path)

    r2 = mo.decide(_rec(cand="pre_20270302_bbbb"), approval=False, models_dir=tmp_path)
    assert r2["adopted"] and (tmp_path / "latest").read_text().strip() == "pre_20270302_bbbb"
    r3 = mo.decide(_rec(cand="pre_20270402_cccc", ok=False), approval=False, models_dir=tmp_path)
    assert not r3["adopted"] and r3["reason"].startswith("切り替えない")


def test_switch_tenkai(tmp_path):
    (tmp_path / "tenkai" / "preview").mkdir(parents=True)
    mo.switch_latest("tenkai_preview", "tenkai_preview_x", tmp_path)
    assert (tmp_path / "tenkai" / "preview" / "latest").read_text().strip() == "tenkai_preview_x"
    mo.switch_latest("preview", "preview_y", tmp_path)
    assert (tmp_path / "preview" / "latest").read_text().strip() == "preview_y"
