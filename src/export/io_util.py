"""JSONの書き出し(アトミックな置き換え)。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from src.export.schema import assert_valid


def dumps(obj: Any, pretty: bool = False) -> str:
    """UTF-8 のまま、キーの順を保って文字列にする。pretty=False は小さくするため空白なし。"""
    if pretty:
        return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_json(path: Path, obj: Any, kind: str | None = None, pretty: bool = False,
               local: bool = False) -> None:
    """検証してから、一時ファイル経由で置き換える(PWAが書きかけのファイルを読まないように)。

    kind を渡すと schema.assert_valid(kind, obj, local) を先に通す。
    local=True はローカルJSON(オッズ・期待値の数値を許す)。
    """
    if kind is not None:
        assert_valid(kind, obj, local=local)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(obj, pretty=pretty)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
