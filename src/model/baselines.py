"""ベースライン B0・B1(spec/03 2.3章)。

- B0: 学習期間のコース別1着率(枠番=コースと仮定)をそのまま p_win にする
- B1: B0 の log確率と全国勝率を説明変数にしたロジスティック回帰。
      出力の logit をレース内で softmax し、温度 T を valid で決める(LightGBM と同じ扱い)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.model.config import N_BOATS, SEED
from src.model.data import fit_temperature, race_softmax, train_row_mask


class B0CourseRate:
    """コース別1着率のベースライン。"""

    name = "B0"

    def __init__(self) -> None:
        self.rates: np.ndarray | None = None  # 枠番1〜6の1着率(合計1に正規化)

    def fit(self, train: pd.DataFrame) -> "B0CourseRate":
        df = train[train_row_mask(train)]
        rates = df.groupby("entry_number")["is_win"].mean().reindex(range(1, N_BOATS + 1))
        if rates.isna().any():
            raise ValueError("学習データに含まれない枠番があります")
        r = rates.to_numpy(dtype=float)
        self.rates = r / r.sum()
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """各行の p_win(レース内で合計1。欠けている艇があれば残りで正規化する)。"""
        if self.rates is None:
            raise RuntimeError("fit が先に必要です")
        scores = np.log(self.rates[df["entry_number"].to_numpy().astype(int) - 1])
        return race_softmax(df["race_id"].to_numpy(), scores, 1.0)

    def params(self) -> dict:
        return {"course_win_rate": [float(x) for x in self.rates]} if self.rates is not None else {}


class B1Logistic:
    """B0 × 全国勝率のロジスティック回帰。

    説明変数: log(B0のコース別1着率)、national_win_rate、national_win_rate のレース内平均との差。
    national_win_rate の欠損は学習期間の中央値で埋める。
    """

    name = "B1"
    columns = ("log_course_rate", "national_win_rate", "win_rate_diff_mean")

    def __init__(self) -> None:
        self.b0 = B0CourseRate()
        self.model = None
        self.fill_value: float = 0.0
        self.temperature: float = 1.0

    def _design(self, df: pd.DataFrame) -> np.ndarray:
        rate = self.b0.rates[df["entry_number"].to_numpy().astype(int) - 1]
        wr = pd.to_numeric(df["national_win_rate"], errors="coerce").fillna(self.fill_value)
        diff = wr - wr.groupby(df["race_id"].to_numpy()).transform("mean")
        return np.column_stack([np.log(rate), wr.to_numpy(float), diff.to_numpy(float)])

    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> "B1Logistic":
        self.b0.fit(train)
        df = train[train_row_mask(train)]
        self.fill_value = float(pd.to_numeric(df["national_win_rate"], errors="coerce").median())
        x = self._design(df)
        y = df["is_win"].to_numpy().astype(int)
        self.model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000, random_state=SEED))
        self.model.fit(x, y)
        if valid is not None:
            v = valid[train_row_mask(valid)]
            self.temperature, _ = fit_temperature(
                v["race_id"].to_numpy(), self.scores(v), v["is_win"].to_numpy().astype(int)
            )
        return self

    def scores(self, df: pd.DataFrame) -> np.ndarray:
        """ロジスティック回帰の logit(温度をかける前)。"""
        if self.model is None:
            raise RuntimeError("fit が先に必要です")
        return self.model.decision_function(self._design(df))

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return race_softmax(df["race_id"].to_numpy(), self.scores(df), self.temperature)

    def params(self) -> dict:
        lr = self.model[-1]
        return {
            "columns": list(self.columns),
            "coef_scaled": [float(c) for c in lr.coef_[0]],
            "intercept": float(lr.intercept_[0]),
            "fill_national_win_rate": self.fill_value,
            "temperature": self.temperature,
        }
