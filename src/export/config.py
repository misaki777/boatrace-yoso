"""エクスポートの設定(spec 07 1章・3章)。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 公開JSON(git 管理・GitHub Pages)と、ローカルJSON(git 管理外。LAN 内のローカル表示用)
DOCS_DATA_DIR = ROOT / "docs" / "data"
LOCAL_DATA_DIR = ROOT / "docs" / "data-local"

# オッズ(締切前・確定)と期待値の「数値」を公開JSONに出すか(spec 07 1章 方針6)。
# 既定は False。True にするのは、BOATRACE振興会の許諾を得たときだけ(利用者確認事項 Q1)。
PUBLISH_ODDS_NUMERIC = False

# 期待値のランク(spec 07 3.2)。上から順に「ev >= 下限」を満たした最初のランク。どれも満たさなければ D
EV_RANK_BOUNDS: tuple[tuple[str, float], ...] = (("A", 1.20), ("B", 1.00), ("C", 0.80))
EV_RANK_LOWEST = "D"
EV_RANKS = ("A", "B", "C", "D")

# 締切前オッズのページと、そのページから取れる券種(spec 07 2.1)
ODDS_PAGES = {
    "odds3t": ("trifecta",),
    "odds2tf": ("exacta", "quinella"),
    "oddstf": ("win", "place"),
    "odds3f": ("trio",),
    "oddsk": ("quinella_place",),
}
DEFAULT_ODDS_PAGES = ("odds3t", "odds2tf", "oddstf")
ODDS_SNAPSHOT_STATUSES = ("ok", "partial", "late", "not_targeted", "stopped")
