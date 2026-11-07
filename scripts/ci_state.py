"""GitHub Actions 用: git 管理外の状態を Cloudflare R2 の非公開バケットから取得・保存する(CI-2。spec/10 2.4)。

毎日のジョブは生データの全履歴を落とさない。R2 の置き場所:

| キー                               | 中身                                                         | 毎日のジョブ |
|------------------------------------|--------------------------------------------------------------|--------------|
| state/history.tar.gz               | 履歴の集計の状態 data/history/state_*.parquet(新しい3つ)    | 取得・保存   |
| state/recent.tar.gz                | 当月・前月の raw(openapi・turnmark)と odds の interim、     | 取得・保存   |
|                                    | 直近400日分の月の interim(races・entries・payouts)、取得ログ |              |
| state/predictions.tar.gz           | 確定済みの予想 data/predictions/ の全部(記録なので消さない) | 取得・保存   |
| state/models.tar.gz                | 採用中のモデル(latest が指すもの。展開予想 models/tenkai/・tenkai2/ も)と、その eval・backtest の要約 | 取得・保存 |
| archive/raw/YYYY-MM.tar.gz         | 月ごとの raw(openapi・turnmark)                             | 前日確定ジョブがその月を保存 |
| archive/interim/YYYY-MM.tar.gz     | 月ごとの interim                                             | 前日確定ジョブがその月を保存 |

| beforeinfo/YYYYMMDD/SS-RR.json     | 直前情報(Worker「beforeinfo」が書く。spec/12)             | 確定ジョブ(lock)が取得 |

| odds/YYYYMMDD/SS-RR.json           | 締切前オッズの記録(Worker「beforeinfo」が書く。spec/13)   | 前日確定ジョブが前日分を取得 |

締切前オッズの記録は data/raw/odds_record/YYYYMMDD/SS-RR.json に置く(特徴量担当が interim の odds_pre を作る)。
公開しない(docs/ には出さない)。recent(当月・前月)と archive の raw に含める(interim を月ごとに作り直すときに要る。
容量は年 約180MB、gzip 後はその数分の1)。_state.json・_stopped.json は取らない。中身はログに出さない。

直前情報は data/raw/beforeinfo/YYYYMMDD/SS-RR.json に置き(F-12b が読む場所)、recent と archive の raw にも入れる
(前日確定で interim を作り直すときにも、直前情報の出どころ pv_source を変えないため)。

archive/ は月1の作り直し(CI-3)と、手元の復元に使う。毎日のジョブでは取得しない。
interim は月単位で作り直される(build_interim はその月の raw を全部読む)ので、
毎日のジョブには当月と前月の raw が必要(前月は、月初の前日確定のため)。
選手カード(racer_cards)は直近10走のために D−400日〜D−1 の interim を読むので、
races・entries・payouts は直近400日にかかる月を持つ(odds は使わないので当月・前月だけ)。

R2 の操作は `npx wrangler r2 object get/put --remote` だけ(S3 互換キーは使わない)。
認証は環境変数 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID(wrangler が読む。表示しない)。

使い方:
    python -m scripts.ci_state pull                      # state/* を取得して上書き展開(手元のファイルは消さない)
    python -m scripts.ci_state push                      # state/* を保存
    python -m scripts.ci_state push --only models        # 種類を絞る(history,recent,predictions,models)
    python -m scripts.ci_state archive-push --months all            # 手元にある全部の月を archive/ に
    python -m scripts.ci_state archive-push --months 2026-11        # 1か月分
    python -m scripts.ci_state archive-pull --months 2026-01..2026-11
    python -m scripts.ci_state archive-pull --months auto --part interim   # 分析用: recent に無い古い月の interim
    python -m scripts.ci_state odds-pull                       # 昨日(JST)の締切前オッズの記録
    python -m scripts.ci_state odds-pull --from 2026-11-07 --to 2026-11-08   # 過去分をまとめて
    python -m scripts.ci_state beforeinfo-pull --date 2026-11-06 --race-ids 20261106-02-01,20261106-12-03
    --dry-run を付けると R2 に触らない(push 系は tar.gz を作ってサイズを表示するまで)

安全のため:
- push の前に全部の tar.gz を作って読み直して確かめる。1つでも失敗・空なら、どれも上げない。
- pull は全部を取得・検証してから展開する。展開は上書きのみで、手元のファイルを消さない。
- 公式サイトの締切前オッズ(data/raw/official_odds/)と旧API(data/raw/legacy/)は対象外。
- data/features/ は対象外(当日分は状態から build_features --date --state で作り直す)。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "br-yoso-data"
JST = timezone(timedelta(hours=9))

KINDS = ("history", "recent", "predictions", "models")
RAW_SOURCES = ("openapi", "turnmark")
BEFOREINFO_DIR = "data/raw/beforeinfo"  # F-12b が読む場所(spec/12 1.5)
BEFOREINFO_WINDOW_MIN = 20
ODDS_RECORD_DIR = "data/raw/odds_record"  # 締切前オッズの記録(spec/13)
ODDS_PULL_JOBS = 8  # odds-pull の同時取得数(1件ずつ wrangler を起動するため)  # 指定が無くても、締切がこの分数以内のレースの直前情報は取りに行く
INTERIM_TABLES = ("races", "entries", "payouts", "odds")
HISTORY_KEEP = 3
INTERIM_LOOKBACK_DAYS = 400  # src/features/racer_cards.py の RESULT_LOOKBACK_DAYS と合わせる
HISTORY_TABLES = ("races", "entries", "payouts")
# データの最初の月(openapi v1 は 2026-01 から。spec/06 U8)。旧API(spec/10 2章)を interim に入れたら
# ワークフローの環境変数 DATA_FROM_MONTH(または --from-month)を 2016-08 にする
DATA_FROM_MONTH = "2026-01"
# 種類ごとに、tar の中に入ってよいパスの先頭
ALLOWED_PREFIXES: dict[str, tuple[str, ...]] = {
    "history": ("data/history/",),
    "recent": ("data/raw/openapi/", "data/raw/turnmark/", "data/raw/beforeinfo/", "data/raw/odds_record/",
               "data/raw/_fetch_log.jsonl",
               "data/interim/"),
    "predictions": ("data/predictions/",),
    "models": ("models/", "reports/eval/", "reports/backtest/"),
    "archive-raw": ("data/raw/openapi/", "data/raw/turnmark/", "data/raw/beforeinfo/", "data/raw/odds_record/"),
    "beforeinfo": ("data/raw/beforeinfo/",),
    "archive-interim": ("data/interim/",),
}


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{n}B"


def today_jst() -> date:
    return datetime.now(JST).date()


# ---------------------------------------------------------------------------
# 月の扱い
# ---------------------------------------------------------------------------


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def prev_month(key: str) -> str:
    y, m = map(int, key.split("-"))
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def recent_months(ref: date) -> list[str]:
    """毎日のジョブで持つ月(前月・当月)。"""
    cur = month_key(ref)
    return [prev_month(cur), cur]


def lookback_months(ref: date, days: int = INTERIM_LOOKBACK_DAYS) -> list[str]:
    """ref − days 〜 ref にかかる月(古い順)。"""
    first, keys, k = month_key(ref - timedelta(days=days)), [], month_key(ref)
    while k >= first:
        keys.append(k)
        k = prev_month(k)
    return sorted(keys)


def months_before_recent(ref: date, from_month: str = DATA_FROM_MONTH) -> list[str]:
    """from_month 〜 recent(直近400日の interim)より前の月。分析(全期間の集計)で archive から足す月。"""
    last = prev_month(lookback_months(ref)[0])
    if from_month > last:
        return []
    return month_range_keys(f"{from_month}..{last}")


def month_range_keys(spec: str) -> list[str]:
    """'2026-11'、'2026-01,2026-03'、'2026-01..2026-11' を月のリストにする。"""
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ".." in part:
            a, b = part.split("..", 1)
            for k in (a, b):
                if not re.fullmatch(r"\d{4}-\d{2}", k):
                    raise ValueError(f"月の指定が不正です: {k}")
            keys, k = [], b
            while k >= a:
                keys.append(k)
                k = prev_month(k)
            out += sorted(keys)
        else:
            if not re.fullmatch(r"\d{4}-\d{2}", part):
                raise ValueError(f"月の指定が不正です: {part}")
            out.append(part)
    return sorted(set(out))


def local_months(root: Path = ROOT) -> list[str]:
    """手元にある raw・interim の月。"""
    keys = set()
    for src in RAW_SOURCES:
        for p in (root / "data/raw" / src).glob("*/*.json.gz"):
            m = re.match(r"(\d{4})(\d{2})\d{2}\.json\.gz$", p.name)
            if m:
                keys.add(f"{m[1]}-{m[2]}")
    for t in INTERIM_TABLES:
        for p in (root / "data/interim" / t).glob("*.parquet"):
            keys.add(p.stem)
    return sorted(keys)


# ---------------------------------------------------------------------------
# 種類ごとのファイルの選び方(リポジトリ直下からの相対パス)
# ---------------------------------------------------------------------------


def _rel(paths, root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in paths if p.is_file())


def raw_files_for_month(key: str, root: Path = ROOT) -> list[str]:
    y, m = key.split("-")
    files = []
    for src in RAW_SOURCES:
        files += (root / "data/raw" / src / y).glob(f"{y}{m}[0-9][0-9].json.gz")
    files += (root / BEFOREINFO_DIR).glob(f"{y}{m}[0-9][0-9]/*.json")
    files += (root / ODDS_RECORD_DIR).glob(f"{y}{m}[0-9][0-9]/[0-9][0-9]-[0-9][0-9].json")
    return _rel(files, root)


def interim_files_for_month(key: str, root: Path = ROOT, tables=INTERIM_TABLES) -> list[str]:
    files = [root / "data/interim" / t / f"{key}.parquet" for t in tables]
    files.append(root / "data/interim/_quality" / f"{key}.json")
    return _rel(files, root)


def history_files(root: Path = ROOT) -> list[str]:
    states = sorted((root / "data/history").glob("state_[0-9]*.parquet"))
    return _rel(states[-HISTORY_KEEP:], root)


def recent_files(ref: date, root: Path = ROOT) -> list[str]:
    files: list[str] = []
    recent = recent_months(ref)
    for key in recent:
        files += raw_files_for_month(key, root) + interim_files_for_month(key, root)
    for key in lookback_months(ref):
        if key not in recent:
            files += interim_files_for_month(key, root, HISTORY_TABLES)
    log = root / "data/raw/_fetch_log.jsonl"
    if log.is_file():
        files.append("data/raw/_fetch_log.jsonl")
    return files


def predictions_files(root: Path = ROOT) -> list[str]:
    return _rel((root / "data/predictions").rglob("*"), root)


def model_ids(root: Path = ROOT) -> list[str]:
    """latest が指すモデルID(pre は models/latest、他は models/<variant>/latest。src/model/lgbm.py と同じ)。"""
    ids = []
    for p in [root / "models/latest", *sorted((root / "models").glob("*/latest"))]:
        if p.is_file():
            mid = p.read_text(encoding="utf-8").strip()
            if mid and mid not in ids:
                ids.append(mid)
    return ids


def variant_of(model_id: str) -> str:
    """'preview_v2_20261105_053e' → 'preview_v2'(末尾の日付とハッシュを外す)。"""
    return re.sub(r"_\d{8}_[0-9a-f]+$", "", model_id)


def models_files(root: Path = ROOT) -> list[str]:
    files = [root / "models/latest", *(root / "models").glob("*/latest")]
    for mid in model_ids(root):
        files += (root / "models" / variant_of(mid) / mid).rglob("*")
        files += (root / "reports/eval" / mid).rglob("*")
        files += (root / "reports/backtest" / mid).glob("*/summary.json")
    # 展開予想: models/{tenkai,tenkai2}/<variant>/latest が指す models/{tenkai,tenkai2}/<variant>/<id>/
    # (src/model/tenkai.py・tenkai2.py)
    for p in sorted([*(root / "models/tenkai").glob("*/latest"), *(root / "models/tenkai2").glob("*/latest")]):
        mid = p.read_text(encoding="utf-8").strip()
        if mid:
            files += [p, *(p.parent / mid).rglob("*")]
    return _rel(files, root)


def files_for(kind: str, ref: date, root: Path = ROOT) -> list[str]:
    if kind == "history":
        return history_files(root)
    if kind == "recent":
        return recent_files(ref, root)
    if kind == "predictions":
        return predictions_files(root)
    if kind == "models":
        return models_files(root)
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# tar.gz
# ---------------------------------------------------------------------------


def is_allowed(name: str, kind: str) -> bool:
    if name.startswith("/") or ".." in Path(name).parts:
        return False
    if name.startswith(("data/raw/official_odds", "data/raw/legacy")):
        return False
    return any(name == p or name.startswith(p) for p in ALLOWED_PREFIXES[kind])


def make_tar(files: list[str], out: Path, kind: str, root: Path = ROOT) -> int:
    if not files:
        raise ValueError(f"{kind}: 対象のファイルがありません")
    with tarfile.open(out, "w:gz", compresslevel=6) as tar:
        for rel in files:
            if not is_allowed(rel, kind):
                raise ValueError(f"{kind}: 対象外のパスです: {rel}")
            tar.add(root / rel, arcname=rel, recursive=False)
    return len(files)


def verify_tar(path: Path, kind: str) -> int:
    """最後まで読み、パスが許可された場所だけかを確かめる。ファイル数を返す。"""
    n = 0
    with tarfile.open(path, "r:gz") as tar:
        for m in tar:
            if not is_allowed(m.name, kind) or not (m.isfile() or m.isdir()):
                raise ValueError(f"想定外のパスを含みます: {m.name}")
            if m.isfile():
                f = tar.extractfile(m)
                if f is not None:
                    while f.read(1 << 20):
                        pass
                n += 1
    return n


def extract_tar(path: Path, kind: str, root: Path = ROOT) -> None:
    """検証済みの tar.gz を上書きで展開する(手元のファイルは消さない)。"""
    verify_tar(path, kind)
    with tarfile.open(path, "r:gz") as tar:
        tar.extractall(root, filter="data")


# ---------------------------------------------------------------------------
# R2
# ---------------------------------------------------------------------------


def state_key(kind: str) -> str:
    return f"{BUCKET}/state/{kind}.tar.gz"


def archive_key(part: str, month: str) -> str:
    return f"{BUCKET}/archive/{part}/{month}.tar.gz"


def wrangler_cmd(action: str, key: str, file: Path) -> list[str]:
    return ["npx", "--yes", "wrangler", "r2", "object", action, key, "--file", str(file), "--remote"]


def run(cmd: list[str], dry_run: bool) -> None:
    print("  $ " + " ".join(cmd), flush=True)
    if not dry_run:
        subprocess.run(cmd, check=True, cwd=tempfile.gettempdir())


def build_all(items: list[tuple[str, str, list[str]]], tmp: Path) -> list[tuple[str, Path]]:
    """(R2 のキー, tar の種類, ファイル) から tar.gz を全部作って検証する。1つでも失敗したら例外。"""
    out, total = [], 0
    for key, kind, files in items:
        f = tmp / (key.replace("/", "_"))
        n = make_tar(files, f, kind)
        if verify_tar(f, kind) != n:
            raise ValueError(f"{key}: 作った tar.gz のファイル数が一致しません")
        size = f.stat().st_size
        total += size
        print(f"[push] {key}: {human(size)}({n} ファイル)", flush=True)
        out.append((key, f))
    print(f"[push] 合計 {human(total)}", flush=True)
    return out


def push(kinds: list[str], ref: date, dry_run: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="ci_state_") as tmp:
        items = [(state_key(k), k, files_for(k, ref)) for k in kinds]
        for key, f in build_all(items, Path(tmp)):
            run(wrangler_cmd("put", key, f), dry_run)
    print("[push] 完了" + ("(dry-run: R2 には触っていません)" if dry_run else ""))
    return 0


def archive_push(months: list[str], dry_run: bool, part: str = "both") -> int:
    with tempfile.TemporaryDirectory(prefix="ci_state_") as tmp:
        items = []
        for m in months:
            if part in ("raw", "both"):
                items.append((archive_key("raw", m), "archive-raw", raw_files_for_month(m)))
            if part in ("interim", "both"):
                items.append((archive_key("interim", m), "archive-interim", interim_files_for_month(m)))
        for key, f in build_all(items, Path(tmp)):
            run(wrangler_cmd("put", key, f), dry_run)
    print("[archive-push] 完了" + ("(dry-run: R2 には触っていません)" if dry_run else ""))
    return 0


def pull_items(items: list[tuple[str, str]], dry_run: bool, label: str) -> int:
    with tempfile.TemporaryDirectory(prefix="ci_state_") as tmp:
        got = []
        # 全部取得・検証してから展開する(途中で失敗したら手元は変えない)
        for key, kind in items:
            f = Path(tmp) / key.replace("/", "_")
            print(f"[{label}] {key}", flush=True)
            run(wrangler_cmd("get", key, f), dry_run)
            if dry_run:
                continue
            n = verify_tar(f, kind)
            print(f"  {human(f.stat().st_size)}({n} ファイル)", flush=True)
            got.append((f, kind))
        for f, kind in got:
            extract_tar(f, kind)
    print(f"[{label}] 完了" + ("(dry-run: 何も変えていません)" if dry_run else ""))
    return 0


RACE_ID_RE = re.compile(r"^(\d{8})-(\d{2})-(\d{2})$")


def parse_race_ids(text: str, d: date) -> list[str]:
    """'20261106-02-01, 20261106-12-03' を検証して返す。形式が違う・日付が違うものはエラー。"""
    out = []
    for rid in re.split(r"[,\s]+", text or ""):
        if not rid:
            continue
        m = RACE_ID_RE.match(rid)
        if not m or m[1] != d.strftime("%Y%m%d") or not (1 <= int(m[2]) <= 24) or not (1 <= int(m[3]) <= 12):
            raise ValueError(f"race_id の形式が違います: {rid[:40]}")
        if rid not in out:
            out.append(rid)
    return out


def races_near_close(d: date, now: datetime, minutes: int = BEFOREINFO_WINDOW_MIN) -> list[str]:
    """当日の interim の races から、締切が now 〜 now+minutes のレース(前の確定ジョブの待ちが
    置き換えられて race_id が届かなかった分を拾うため)。"""
    import pandas as pd
    from src.features.build_interim import read_interim  # 特徴量担当の関数(読むだけ)

    try:
        races = read_interim("races", d, d)
    except (OSError, ValueError):
        return []
    if races.empty:
        return []
    closed = pd.to_datetime(races["closed_at"], utc=True)
    now_ts = pd.Timestamp(now).tz_convert("UTC")
    mask = (closed >= now_ts) & (closed <= now_ts + pd.Timedelta(minutes=minutes))
    return sorted(races.loc[mask, "race_id"].astype(str))


def beforeinfo_key(race_id: str) -> str:
    ymd, ss, rr = RACE_ID_RE.match(race_id).groups()
    return f"{BUCKET}/beforeinfo/{ymd}/{ss}-{rr}.json"


def beforeinfo_path(race_id: str, root: Path = ROOT) -> Path:
    ymd, ss, rr = RACE_ID_RE.match(race_id).groups()
    return root / BEFOREINFO_DIR / ymd / f"{ss}-{rr}.json"


def beforeinfo_pull(race_ids: list[str], dry_run: bool) -> int:
    """直前情報を1レースずつ取得する。無いレースは飛ばす(Open API の直前情報で代わりに動く)。
    内容(展示タイム等)はログに出さない。"""
    got = 0
    for rid in race_ids:
        dest = beforeinfo_path(rid)
        cmd = wrangler_cmd("get", beforeinfo_key(rid), dest)
        print("  $ " + " ".join(cmd), flush=True)
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        cmd[cmd.index("--file") + 1] = str(tmp)
        r = subprocess.run(cmd, cwd=tempfile.gettempdir(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            print(f"  {rid}: R2 にありません(Open API の直前情報で続けます)", flush=True)
            tmp.unlink(missing_ok=True)
            continue
        try:
            json.loads(tmp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"  {rid}: JSON として読めないので使いません", flush=True)
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(dest)
        got += 1
    print(f"[beforeinfo-pull] {got}/{len(race_ids)} レース" + ("(dry-run)" if dry_run else ""))
    return 0


def odds_key(race_id: str) -> str:
    ymd, ss, rr = RACE_ID_RE.match(race_id).groups()
    return f"{BUCKET}/odds/{ymd}/{ss}-{rr}.json"


def odds_path(race_id: str, root: Path = ROOT) -> Path:
    ymd, ss, rr = RACE_ID_RE.match(race_id).groups()
    return root / ODDS_RECORD_DIR / ymd / f"{ss}-{rr}.json"


def odds_race_ids(state: dict, d: date) -> list[str]:
    """Worker の odds/YYYYMMDD/_state.json の races から、その日の race_id を取り出す(形式を確かめる)。
    R2 の一覧を取る手段が wrangler に無いので、状態に載っているレースを1件ずつ取りに行く。"""
    ymd = d.strftime("%Y%m%d")
    out = []
    for rid in (state.get("races") or {}):
        m = RACE_ID_RE.match(str(rid))
        if m and m[1] == ymd:
            out.append(str(rid))
    return sorted(set(out))


def _get_json(key: str, dest: Path) -> dict | None:
    """R2 から1件取り、JSON として読めたら dict を返す(中身は表示しない)。"""
    tmp = dest.with_name(dest.name + ".tmp")
    cmd = wrangler_cmd("get", key, tmp)
    r = subprocess.run(cmd, cwd=tempfile.gettempdir(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        return None
    try:
        data = json.loads(tmp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tmp.unlink(missing_ok=True)
        return None
    if not isinstance(data, dict):
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(dest)
    return data


def odds_pull_day(d: date, dry_run: bool, force: bool = False, jobs: int = ODDS_PULL_JOBS) -> tuple[int, int]:
    """1日分の締切前オッズの記録を取る。(取得した数, 対象の数) を返す。手元にあるレースは --force が無ければ取らない。"""
    from concurrent.futures import ThreadPoolExecutor

    ymd = d.strftime("%Y%m%d")
    state_key_ = f"{BUCKET}/odds/{ymd}/_state.json"
    print(f"[odds-pull] {d}", flush=True)
    print("  $ " + " ".join(wrangler_cmd("get", state_key_, Path("_state.json"))), flush=True)
    if dry_run:
        print("  $ (_state.json の races の各レースについて) " +
              " ".join(wrangler_cmd("get", f"{BUCKET}/odds/{ymd}/SS-RR.json",
                                    Path(f"{ODDS_RECORD_DIR}/{ymd}/SS-RR.json"))), flush=True)
        return 0, 0
    with tempfile.TemporaryDirectory(prefix="ci_odds_") as tmp:
        state = _get_json(state_key_, Path(tmp) / "_state.json")
    if state is None:
        print("  記録がありません(Worker がその日にオッズを取っていない)", flush=True)
        return 0, 0
    ids = odds_race_ids(state, d)
    todo = [rid for rid in ids if force or not odds_path(rid).exists()]
    if todo:
        odds_path(todo[0]).parent.mkdir(parents=True, exist_ok=True)

    def one(rid: str) -> bool:
        data = _get_json(odds_key(rid), odds_path(rid))
        if data is not None and data.get("race_id") not in (None, rid):
            odds_path(rid).unlink(missing_ok=True)  # 別のレースの中身なら使わない
            return False
        return data is not None

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        got = sum(ex.map(one, todo))
    print(f"  {got}/{len(todo)} レースを取得(状態に載っているレース {len(ids)}、手元に既にある分は飛ばした)", flush=True)
    return got, len(todo)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="R2 との状態の取得・保存")
    p.add_argument("action", choices=["pull", "push", "archive-pull", "archive-push", "beforeinfo-pull", "odds-pull"])
    p.add_argument("--from", dest="d_from", help="odds-pull の開始日(--to と一緒に)")
    p.add_argument("--to", dest="d_to", help="odds-pull の終了日")
    p.add_argument("--force", action="store_true", help="odds-pull: 手元にあるレースも取り直す")
    p.add_argument("--date", help="beforeinfo-pull の日(既定: 今日 JST)")
    p.add_argument("--race-ids", default="", help="beforeinfo-pull のレース(カンマ・空白区切り)")
    p.add_argument("--dry-run", action="store_true", help="R2 に触らない")
    p.add_argument("--only", help=f"pull/push の種類を絞る(カンマ区切り。{','.join(KINDS)})")
    p.add_argument("--ref-date", help="recent の月を決める日(既定: 今日 JST)。YYYY-MM-DD")
    p.add_argument("--months", help="archive の月: 2026-11 / 2026-01,2026-02 / 2026-01..2026-11 / "
                   "all(push のみ)/ auto(pull のみ。recent より前の月)")
    p.add_argument("--part", choices=["raw", "interim", "both"], default="both", help="archive の対象")
    p.add_argument("--from-month", default=None, help=f"auto の開始月(既定: 環境変数 DATA_FROM_MONTH か {DATA_FROM_MONTH})")
    a = p.parse_args(argv)
    a.kinds = list(KINDS)
    if a.only:
        a.kinds = [k.strip() for k in a.only.split(",") if k.strip()]
        bad = [k for k in a.kinds if k not in KINDS]
        if bad:
            p.error(f"不明な種類: {','.join(bad)}")
    a.ref = date.fromisoformat(a.ref_date) if a.ref_date else today_jst()
    a.day = date.fromisoformat(a.date) if a.date else today_jst()
    if a.action.startswith("archive"):
        if not a.months:
            p.error("--months を指定してください")
        if a.months == "auto":
            if a.action != "archive-pull":
                p.error("auto は archive-pull だけで使えます")
            import os
            a.month_list = months_before_recent(a.ref, a.from_month or os.environ.get("DATA_FROM_MONTH") or DATA_FROM_MONTH)
        elif a.months == "all":
            if a.action == "archive-pull":
                p.error("archive-pull では月を指定してください(例: 2026-01..2026-11)")
            a.month_list = local_months()
        else:
            try:
                a.month_list = month_range_keys(a.months)
            except ValueError as e:
                p.error(str(e))
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    try:
        if a.action == "odds-pull":
            if a.d_from or a.d_to:
                d0 = date.fromisoformat(a.d_from or a.d_to)
                d1 = date.fromisoformat(a.d_to or a.d_from)
            else:
                d0 = d1 = date.fromisoformat(a.date) if a.date else today_jst() - timedelta(days=1)
            if d1 < d0 or (d1 - d0).days > 400:
                raise ValueError("odds-pull の期間が不正です")
            d = d0
            while d <= d1:
                odds_pull_day(d, a.dry_run, a.force)
                d += timedelta(days=1)
            print("[odds-pull] 完了" + ("(dry-run: R2 には触っていません)" if a.dry_run else ""))
            return 0
        if a.action == "beforeinfo-pull":
            ids = parse_race_ids(a.race_ids, a.day)
            near = races_near_close(a.day, datetime.now(JST)) if a.day == today_jst() else []
            return beforeinfo_pull(sorted(set(ids) | set(near)), a.dry_run)
        if a.action == "push":
            return push(a.kinds, a.ref, a.dry_run)
        if a.action == "pull":
            return pull_items([(state_key(k), k) for k in a.kinds], a.dry_run, "pull")
        if a.action == "archive-push":
            return archive_push(a.month_list, a.dry_run, a.part)
        if not a.month_list:
            print("[archive-pull] 取得する月はありません(recent で足りています)")
            return 0
        items = []
        for m in a.month_list:
            if a.part in ("raw", "both"):
                items.append((archive_key("raw", m), "archive-raw"))
            if a.part in ("interim", "both"):
                items.append((archive_key("interim", m), "archive-interim"))
        return pull_items(items, a.dry_run, "archive-pull")
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as e:
        print(f"[{a.action}] 失敗しました: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
