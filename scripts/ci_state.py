"""GitHub Actions 用: git 管理外の状態を Cloudflare R2 の非公開バケットから取得・保存する(CI-2。spec/10 2.4)。

毎日のジョブは生データの全履歴を落とさない。R2 の置き場所:

| キー                               | 中身                                                         | 毎日のジョブ |
|------------------------------------|--------------------------------------------------------------|--------------|
| state/history.tar.gz               | 履歴の集計の状態 data/history/state_*.parquet(新しい3つ)    | 取得・保存   |
| state/recent.tar.gz                | 当月・前月の raw(openapi・turnmark)と odds の interim、     | 取得・保存   |
|                                    | 直近400日分の月の interim(races・entries・payouts)、取得ログ |              |
| state/predictions.tar.gz           | 確定済みの予想 data/predictions/ の全部(記録なので消さない) | 取得・保存   |
| state/models.tar.gz                | 採用中のモデル(latest が指すもの。展開予想 models/tenkai/ も)と、その eval・backtest の要約 | 取得・保存 |
| archive/raw/YYYY-MM.tar.gz         | 月ごとの raw(openapi・turnmark)                             | 前日確定ジョブがその月を保存 |
| archive/interim/YYYY-MM.tar.gz     | 月ごとの interim                                             | 前日確定ジョブがその月を保存 |

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
    --dry-run を付けると R2 に触らない(push 系は tar.gz を作ってサイズを表示するまで)

安全のため:
- push の前に全部の tar.gz を作って読み直して確かめる。1つでも失敗・空なら、どれも上げない。
- pull は全部を取得・検証してから展開する。展開は上書きのみで、手元のファイルを消さない。
- 公式サイトの締切前オッズ(data/raw/official_odds/)と旧API(data/raw/legacy/)は対象外。
- data/features/ は対象外(当日分は状態から build_features --date --state で作り直す)。
"""

from __future__ import annotations

import argparse
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
    "recent": ("data/raw/openapi/", "data/raw/turnmark/", "data/raw/_fetch_log.jsonl", "data/interim/"),
    "predictions": ("data/predictions/",),
    "models": ("models/", "reports/eval/", "reports/backtest/"),
    "archive-raw": ("data/raw/openapi/", "data/raw/turnmark/"),
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
    # 展開予想: models/tenkai/<variant>/latest が指す models/tenkai/<variant>/<id>/(src/model/tenkai.py)
    for p in sorted((root / "models/tenkai").glob("*/latest")):
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="R2 との状態の取得・保存")
    p.add_argument("action", choices=["pull", "push", "archive-pull", "archive-push"])
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
