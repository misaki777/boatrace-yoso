"""生データ(data/raw/)の保存と読み込み。

他の担当(特徴量担当など)は load_raw() を import して使うだけにする。
保存形式: data/raw/{source}/{YYYY}/{YYYYMMDD}.json.gz
  (レスポンスのバイト列をそのまま gzip したもの。再整形はしない)
締切前オッズ(公式サイト)は load_official_odds(race_id) で読む(保存形式は下の official_odds の節)。
"""

import gzip
import json
import os
from datetime import date, datetime
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
FETCH_LOG_NAME = "_fetch_log.jsonl"
SOURCES = ("turnmark", "openapi")
# 旧API(v3。2016-08-01〜2025-12-31)。種類ごとに別ファイル。ソース名は "legacy_{kind}"
LEGACY_KINDS = ("programs", "previews", "results")
LEGACY_SOURCES = tuple(f"legacy_{k}" for k in LEGACY_KINDS)


def _source_dir(source: str) -> Path:
    """ソース名から data/raw/ 以下の相対ディレクトリを返す。"""
    if source in SOURCES:
        return Path(source)
    if source in LEGACY_SOURCES:
        return Path("legacy") / source.removeprefix("legacy_")
    raise ValueError(f"未知のソースです: {source}")


def to_date(value: date | str) -> date:
    """date / 'YYYY-MM-DD' / 'YYYYMMDD' を date に変換する。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return date.fromisoformat(text)


def raw_path(source: str, target: date | str, raw_dir: Path | None = None) -> Path:
    """ソースと日付から保存先のパスを返す(ファイルの有無は見ない)。"""
    d = to_date(target)
    base = Path(raw_dir) if raw_dir is not None else RAW_DIR
    return base / _source_dir(source) / f"{d.year:04d}" / f"{d.strftime('%Y%m%d')}.json.gz"


def load_raw(source: str, target: date | str, raw_dir: Path | None = None) -> dict | None:
    """指定ソース・指定日の生データを dict で返す。ファイルが無ければ None。

    source: "turnmark" または "openapi"
    target: date、または 'YYYY-MM-DD' / 'YYYYMMDD' の文字列
    """
    path = raw_path(source, target, raw_dir)
    if not path.exists():
        return None
    with gzip.open(path, "rb") as f:
        return json.loads(f.read())


def load_raw_any(
    target: date | str,
    sources: tuple[str, ...] = SOURCES,
    raw_dir: Path | None = None,
) -> tuple[str, dict] | None:
    """sources の順に探し、最初に見つかった (source, dict) を返す。どれも無ければ None。

    既定は turnmark 優先、無ければ openapi(turnmark が欠けた日の代わり)。
    """
    for source in sources:
        data = load_raw(source, target, raw_dir)
        if data is not None:
            return source, data
    return None


def load_legacy(kind: str, target: date | str, raw_dir: Path | None = None) -> dict | None:
    """旧API(v3)の1日分を dict で返す。kind: "programs" / "previews" / "results"。無ければ None。

    中身は {kind: [レース, ...]} (例: {"programs": [{"stadium_number": 1, "number": 1, "boats": [...]}, ...]})。
    形式の詳細は src/collect/LEGACY_FORMAT.md。
    """
    if kind not in LEGACY_KINDS:
        raise ValueError(f"未知の種類です: {kind}")
    return load_raw(f"legacy_{kind}", target, raw_dir)


def list_raw_dates(source: str, raw_dir: Path | None = None) -> list[date]:
    """保存済みの日付を昇順で返す。"""
    base = (Path(raw_dir) if raw_dir is not None else RAW_DIR) / _source_dir(source)
    if not base.exists():
        return []
    dates = []
    for path in base.glob("*/*.json.gz"):
        stem = path.name.removesuffix(".json.gz")
        try:
            dates.append(to_date(stem))
        except ValueError:
            continue
    return sorted(dates)


def write_raw_bytes(source: str, target: date | str, body: bytes, raw_dir: Path | None = None) -> Path:
    """レスポンスのバイト列を gzip で保存する。一時ファイルに書いてから rename する。"""
    path = raw_path(source, target, raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)
    return path


def fetch_log_path(raw_dir: Path | None = None) -> Path:
    return (Path(raw_dir) if raw_dir is not None else RAW_DIR) / FETCH_LOG_NAME


def read_fetch_log(raw_dir: Path | None = None) -> list[dict]:
    """取得ログを全件読む。壊れた行は読み飛ばす。"""
    path = fetch_log_path(raw_dir)
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_fetch_log(record: dict, raw_dir: Path | None = None) -> None:
    """取得ログに1行追記する。"""
    path = fetch_log_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---- 締切前オッズ(公式サイト。spec/07 2.6) ----
# 保存先: data/raw/official_odds/{YYYY}/{YYYYMMDD}/
#   {場2桁}-{R2桁}_{page}_T{分}.html.gz  取得したHTMLそのまま
#   {場2桁}-{R2桁}_T{分}.json.gz         解析結果(late のときも status だけ書く)
#   _stopped.json                        その日の取得を止めたときの印(再開は手動)
#   _targets.json                        取得の対象・対象外(not_targeted)・中止のレース一覧(ループ・一括取得が書く)
OFFICIAL_ODDS_DIR = "official_odds"


def official_odds_day_dir(target: date | str, raw_dir: Path | None = None) -> Path:
    """締切前オッズの、その日の保存ディレクトリ。"""
    d = to_date(target)
    base = Path(raw_dir) if raw_dir is not None else RAW_DIR
    return base / OFFICIAL_ODDS_DIR / f"{d.year:04d}" / d.strftime("%Y%m%d")


def _split_race_id(race_id: str) -> tuple[str, str]:
    """'20261103-02-01' → ('20261103', '02-01')。"""
    parts = race_id.split("-")
    if len(parts) != 3 or len(parts[0]) != 8 or not all(p.isdigit() for p in parts):
        raise ValueError(f"race_id の形式が不正です: {race_id}")
    return parts[0], f"{int(parts[1]):02d}-{int(parts[2]):02d}"


def official_odds_paths(race_id: str, timing: str, page: str | None = None, raw_dir: Path | None = None) -> Path:
    """解析JSON(page=None)または生HTML(page 指定)のパス。timing は 'T05' の形。"""
    ymd, key = _split_race_id(race_id)
    day_dir = official_odds_day_dir(ymd, raw_dir)
    if page is None:
        return day_dir / f"{key}_{timing}.json.gz"
    return day_dir / f"{key}_{page}_{timing}.html.gz"


def write_gzip_atomic(path: Path, body: bytes) -> Path:
    """gzip で一時ファイルに書いてから rename する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)
    return path


def list_official_odds(race_id: str, raw_dir: Path | None = None) -> list[dict]:
    """そのレースの解析JSONを全部読む(取得の古い順)。"""
    ymd, key = _split_race_id(race_id)
    day_dir = official_odds_day_dir(ymd, raw_dir)
    if not day_dir.exists():
        return []
    items = []
    for path in day_dir.glob(f"{key}_T*.json.gz"):
        try:
            with gzip.open(path, "rb") as f:
                data = json.loads(f.read())
        except (OSError, json.JSONDecodeError):
            continue
        data.setdefault("timing", path.name.removesuffix(".json.gz").split("_")[-1])
        items.append(data)
    # fetched_at が無いもの(late)は古い側に並べる
    items.sort(key=lambda d: (d.get("fetched_at") is not None, d.get("fetched_at") or ""))
    return items


def load_official_odds(race_id: str, timing: str | int | None = None, raw_dir: Path | None = None) -> dict | None:
    """締切前オッズの解析結果を返す。無ければ None。

    race_id: '20261103-02-01'
    timing: None なら最新(fetched_at が最も新しいもの。取れたものが無ければ late の記録)。
            5 や 'T05' を渡すと、その「締切何分前」の取得分だけを返す。
    戻り値の形は spec/07 2.6(status が 'ok' / 'partial' / 'late')。
    """
    items = list_official_odds(race_id, raw_dir)
    if timing is not None:
        label = timing if isinstance(timing, str) else f"T{int(timing):02d}"
        items = [d for d in items if d.get("timing") == label]
    return items[-1] if items else None


def load_official_odds_stopped(target: date | str, raw_dir: Path | None = None) -> dict | None:
    """その日の締切前オッズの取得を止めていれば、その理由などの dict を返す。止めていなければ None。"""
    path = official_odds_day_dir(target, raw_dir) / "_stopped.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"reason": "unknown"}


def load_official_odds_targets(target: date | str, raw_dir: Path | None = None) -> dict | None:
    """その日の取得対象の記録(_targets.json)。無ければ None。

    形: {"date", "updated_at", "pages": [...], "targeted": [race_id...], "not_targeted": [...], "canceled": [...]}
    """
    path = official_odds_day_dir(target, raw_dir) / "_targets.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def official_odds_status(race_id: str, raw_dir: Path | None = None) -> str | None:
    """レースの締切前オッズの状態を、spec/07 3.3 の odds_snapshot.status の語で返す。

    - 解析JSONがあれば、その最新の status('ok' / 'partial' / 'late' / 'failed')
    - 無ければ: 絞り込みで対象外なら 'not_targeted'、その日の取得を止めていれば 'stopped'
    - どれでもなければ None(まだ取っていない、またはその日は取得していない)
    """
    doc = load_official_odds(race_id, raw_dir=raw_dir)
    if doc is not None:
        return doc.get("status")
    ymd, _ = _split_race_id(race_id)
    targets = load_official_odds_targets(ymd, raw_dir)
    if targets and race_id in targets.get("not_targeted", []):
        return "not_targeted"
    if load_official_odds_stopped(ymd, raw_dir) is not None:
        return "stopped"
    return None
