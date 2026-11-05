"""アプリの版番号が sw.js と about.js でそろっていることを確かめる。

sw.js の APP_VERSION を上げないと、端末に古いアプリがキャッシュされたまま残るため。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _version(path: Path) -> str:
    m = re.search(r"APP_VERSION\s*=\s*'([0-9.]+)'", path.read_text(encoding="utf-8"))
    assert m, f"APP_VERSION が見つかりません: {path}"
    return m.group(1)


def test_app_version_matches():
    assert _version(ROOT / "docs/sw.js") == _version(ROOT / "docs/app/views/about.js")
