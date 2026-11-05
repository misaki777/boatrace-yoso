"""GitHub Actions 用: git 管理外の状態(生データ・中間データ・予想・モデル)を
Cloudflare R2 の非公開バケットから取得・保存する。

種類ごとに tar.gz を1つ作り、R2 の state/<種類>.tar.gz に置く。
R2 の操作は `npx wrangler r2 object get/put --remote` だけを使う(S3 互換キーは使わない)。
認証は環境変数 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID(wrangler が読む。このスクリプトは表示しない)。

使い方:
    python -m scripts.ci_state pull                # R2 → 手元に展開
    python -m scripts.ci_state push                # 手元 → R2(初回のアップロードにも使う)
    python -m scripts.ci_state push --dry-run      # tar.gz を作ってサイズを表示するだけ(R2 に触らない)
    python -m scripts.ci_state pull --dry-run      # 実行するコマンドを表示するだけ
    python -m scripts.ci_state push --only models  # 種類を絞る

安全のため:
- push の前に、各ディレクトリが存在して空でないこと、作った tar.gz が読み直せることを確かめる。
  1つでも失敗したら、どれも上げない(壊れた状態で上書きしない)。
- data/features/ は対象外。当日分は raw と interim から build_features --date で作り直せる
  (ジョブ内で必要な変種だけ作る。学習用の features_*.parquet は Actions では使わない)。
- 公式サイトの締切前オッズ(data/raw/official_odds/)は対象外(Actions では使わない・広げない)。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "br-yoso-data"
PREFIX = "state"

# 種類名 → リポジトリ直下からの相対パス
KINDS: dict[str, str] = {
    "raw": "data/raw",
    "interim": "data/interim",
    "predictions": "data/predictions",
    "models": "models",
}
# tar に入れないもの(リポジトリ直下からの相対パスの先頭)
EXCLUDES = ("data/raw/official_odds",)


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{n}B"


def object_key(kind: str) -> str:
    return f"{BUCKET}/{PREFIX}/{kind}.tar.gz"


def wrangler_cmd(action: str, kind: str, file: Path) -> list[str]:
    """wrangler のコマンド(get / put)。"""
    return ["npx", "--yes", "wrangler", "r2", "object", action, object_key(kind), "--file", str(file), "--remote"]


def is_excluded(rel: str) -> bool:
    return any(rel == e or rel.startswith(e + "/") for e in EXCLUDES)


def make_tar(kind: str, out: Path, root: Path = ROOT) -> int:
    """root/KINDS[kind] を tar.gz にする。入れたファイル数を返す。"""
    base = root / KINDS[kind]
    if not base.is_dir():
        raise FileNotFoundError(f"{KINDS[kind]} がありません")
    count = 0
    with tarfile.open(out, "w:gz", compresslevel=6) as tar:
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if is_excluded(rel) or "__pycache__" in path.parts:
                continue
            if path.is_file() or path.is_symlink():
                tar.add(path, arcname=rel, recursive=False)
                count += 1
            elif path.is_dir():
                tar.add(path, arcname=rel, recursive=False)
    if count == 0:
        raise ValueError(f"{KINDS[kind]} にファイルがありません")
    return count


def verify_tar(path: Path, kind: str) -> int:
    """tar.gz を最後まで読み、パスが想定のディレクトリの中だけかを確かめる。ファイル数を返す。"""
    prefix = KINDS[kind]
    n = 0
    with tarfile.open(path, "r:gz") as tar:
        for m in tar:
            name = m.name
            if name.startswith("/") or ".." in Path(name).parts or not (name == prefix or name.startswith(prefix + "/")):
                raise ValueError(f"想定外のパスを含みます: {name}")
            if m.isfile():
                f = tar.extractfile(m)
                if f is not None:
                    while f.read(1 << 20):
                        pass
                n += 1
    return n


def extract_tar(path: Path, kind: str, root: Path = ROOT) -> None:
    """検証済みの tar.gz を、既存のディレクトリを置き換える形で展開する。"""
    verify_tar(path, kind)
    target = root / KINDS[kind]
    staging = Path(tempfile.mkdtemp(dir=root, prefix=".ci_state_"))
    try:
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(staging, filter="data")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        (staging / KINDS[kind]).rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def run(cmd: list[str], dry_run: bool) -> None:
    print("  $ " + " ".join(cmd), flush=True)
    if dry_run:
        return
    # 出力はそのまま流す(wrangler はトークンを表示しない)
    subprocess.run(cmd, check=True, cwd=tempfile.gettempdir())


def pull(kinds: list[str], dry_run: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="ci_state_") as tmp:
        files = {}
        # まず全部取得して検証してから展開する(途中で失敗したら手元は変えない)
        for kind in kinds:
            f = Path(tmp) / f"{kind}.tar.gz"
            print(f"[pull] {kind}", flush=True)
            run(wrangler_cmd("get", kind, f), dry_run)
            if dry_run:
                continue
            n = verify_tar(f, kind)
            print(f"  {kind}: {human(f.stat().st_size)}({n} ファイル)", flush=True)
            files[kind] = f
        for kind, f in files.items():
            extract_tar(f, kind)
    print("[pull] 完了" + ("(dry-run: 何も変えていません)" if dry_run else ""))
    return 0


def push(kinds: list[str], dry_run: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="ci_state_") as tmp:
        files = {}
        total = 0
        # まず全部作って検証する。1つでも失敗したら何も上げない
        for kind in kinds:
            f = Path(tmp) / f"{kind}.tar.gz"
            n = make_tar(kind, f)
            if verify_tar(f, kind) != n:
                raise ValueError(f"{kind}: 作った tar.gz のファイル数が一致しません")
            size = f.stat().st_size
            total += size
            print(f"[push] {kind}: {human(size)}({n} ファイル)", flush=True)
            files[kind] = f
        print(f"[push] 合計 {human(total)}", flush=True)
        for kind, f in files.items():
            run(wrangler_cmd("put", kind, f), dry_run)
    print("[push] 完了" + ("(dry-run: R2 には触っていません)" if dry_run else ""))
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="R2 との状態の取得・保存")
    p.add_argument("action", choices=["pull", "push"])
    p.add_argument("--dry-run", action="store_true", help="R2 に触らない(push は tar.gz の作成とサイズ表示まで)")
    p.add_argument("--only", help=f"種類を絞る(カンマ区切り。{','.join(KINDS)})")
    a = p.parse_args(argv)
    kinds = list(KINDS)
    if a.only:
        kinds = [k.strip() for k in a.only.split(",") if k.strip()]
        bad = [k for k in kinds if k not in KINDS]
        if bad:
            p.error(f"不明な種類: {','.join(bad)}")
    a.kinds = kinds
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    try:
        return pull(a.kinds, a.dry_run) if a.action == "pull" else push(a.kinds, a.dry_run)
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as e:
        print(f"[{a.action}] 失敗しました: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
