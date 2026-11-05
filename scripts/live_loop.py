"""当日の 収集 → 予想 → export を回す統合スクリプト(P-2。spec 07 2.7・5章)。

各担当のモジュールは CLI(python -m ...)として子プロセスで呼ぶだけで、中身には立ち入らない。
外部へのアクセスは収集担当のモジュール(src.collect.*)だけが行う。このスクリプト自身は通信しない。

使い方:
    source .venv/bin/activate
    python -m scripts.live_loop --dry-run              # 実行する予定(コマンドと時刻)を表示するだけ
    python -m scripts.live_loop --prepare-only         # 朝の準備(取得→特徴量→予想→export)を1回だけ
    python -m scripts.live_loop                        # 常駐: 準備 → 締切前オッズの取得ループ → 定期の結果反映 → 終了処理
    python -m scripts.live_loop --no-odds              # 締切前オッズを取らない(予想と結果反映だけ)

流れ:
    1. 準備: fetch(openapi 当日) → build_interim → build_features(pre) → predict(pre) → export_day → export_index
    2. 締切前オッズ: src.collect.official_odds --loop を子プロセスで起動する(スケジューラは収集担当の側)。
       data/raw/official_odds/YYYY/YYYYMMDD/*.json.gz が増えたら export_day --odds-only で JSON を更新する。
       --only-recommended のときは、準備で書き出した docs/data/days/YYYYMMDD.json から推奨買い目のある
       race_id を一時ファイルに書き、--race-ids-file で渡す(収集担当の CLI は推奨の有無を知らないため)。
       収集が止まった日(_stopped.json。403・429・連続失敗)は再開しない。確かめてから --resume を付けて起動し直す。
    3. 結果反映: --refresh-minutes(既定30分)ごとに fetch(openapi) → build_interim → export_day → export_index。
       openapi の再取得は、収集担当の fetch が「前回から10分未満ならスキップ」するので、二重に取りに行かない。
    4. 終了: 最終レースの締切から --finish-after-minutes(既定40分)たったら、最後に結果反映を1回して終わる。
       Ctrl+C でも子プロセスを止めて終わる。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OFFICIAL_ODDS_MODULE = ROOT / "src" / "collect" / "official_odds.py"
DOCS_DATA_DIR = ROOT / "docs" / "data"
VENV_EXTRA_LIB = ROOT / ".venv" / "lib" / "extra"  # LightGBM 用の libgomp(spec 06 U9)

# 子プロセスの1回あたりの上限時間(秒)。収集の待機を考えて長めにする
STEP_TIMEOUT_SEC = 30 * 60
# ループの1周の間隔(秒)。オッズのファイルの増加をこの間隔で見る
TICK_SEC = 30


def now_jst() -> datetime:
    return datetime.now(JST)


# ---------------------------------------------------------------------------
# コマンドの組み立て(副作用なし。テスト対象)
# ---------------------------------------------------------------------------


def module_cmd(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def prepare_commands(d: date, variant: str = "pre") -> list[list[str]]:
    """朝の準備のコマンド列。"""
    ds = d.isoformat()
    return [
        module_cmd("src.collect.fetch", "--source", "openapi", "--date", ds),
        module_cmd("src.features.build_interim", "--from", ds, "--to", ds),
        module_cmd("src.features.build_features", "--variant", variant, "--date", ds),
        module_cmd("src.model.predict", "--date", ds, "--variant", variant),
        module_cmd("src.export.export_day", "--date", ds),
        module_cmd("src.export.export_index"),
    ]


def refresh_commands(d: date) -> list[list[str]]:
    """結果反映のコマンド列(予想はやり直さない)。"""
    ds = d.isoformat()
    return [
        module_cmd("src.collect.fetch", "--source", "openapi", "--date", ds),
        module_cmd("src.features.build_interim", "--from", ds, "--to", ds),
        module_cmd("src.export.export_day", "--date", ds),
        module_cmd("src.export.export_index"),
    ]


def odds_loop_command(d: date, minutes_before: int, pages: str | None, stadiums: str | None,
                      race_ids_file: str | None, sleep: float | None, resume: bool = False) -> list[str]:
    """締切前オッズの取得ループ(収集担当の CLI。spec 07 2.7・2.8)。"""
    args = ["--date", d.isoformat(), "--loop", "--minutes-before", str(minutes_before)]
    if pages:
        args += ["--pages", pages]
    if stadiums:
        args += ["--stadiums", stadiums]
    if race_ids_file:
        args += ["--race-ids-file", race_ids_file]
    if sleep is not None:
        args += ["--sleep", str(sleep)]
    if resume:
        args.append("--resume")
    return module_cmd("src.collect.official_odds", *args)


def recommended_race_ids(day_json: dict | None) -> list[str]:
    """公開JSON(days/YYYYMMDD.json)から、推奨買い目が1点以上あるレースの race_id を返す。"""
    out: list[str] = []
    for st in (day_json or {}).get("stadiums") or []:
        for race in st.get("races") or []:
            pred = race.get("prediction") or {}
            if pred.get("recommended"):
                out.append(race["race_id"])
    return sorted(out)


def load_day_json(d: date, data_dir: Path = DOCS_DATA_DIR) -> dict | None:
    path = data_dir / "days" / f"{d:%Y%m%d}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_race_ids_file(race_ids: list[str]) -> str:
    """--race-ids-file 用の一時ファイル(1行1つ)。終了時に消す。"""
    f = tempfile.NamedTemporaryFile("w", suffix="_race_ids.txt", prefix="boatrace_", delete=False, encoding="utf-8")
    with f:
        f.write("\n".join(race_ids) + ("\n" if race_ids else ""))
    return f.name


def stopped_marker(d: date, raw_dir: Path = RAW_DIR) -> Path:
    """収集担当が、その日の取得を止めたときに置く印(spec 07 2.5)。"""
    return odds_dir(d, raw_dir) / "_stopped.json"


def odds_export_command(d: date) -> list[str]:
    return module_cmd("src.export.export_day", "--date", d.isoformat(), "--odds-only")


# ---------------------------------------------------------------------------
# 当日の締切時刻・オッズのファイル(読むだけ)
# ---------------------------------------------------------------------------


def closing_times(raw: dict | None) -> list[datetime]:
    """openapi の生JSONから、締切時刻(JST)の一覧を昇順で返す。中止などで null のものは除く。"""
    if not raw:
        return []
    out: list[datetime] = []
    stadiums = ((raw.get("programs") or {}).get("stadiums") or {})
    for st in stadiums.values():
        for race in ((st or {}).get("races") or {}).values():
            ca = (race or {}).get("closed_at")
            if not ca:
                continue
            try:
                out.append(datetime.strptime(ca, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST))
            except ValueError:
                continue
    return sorted(out)


def load_openapi_raw(d: date, raw_dir: Path = RAW_DIR) -> dict | None:
    """data/raw/openapi/YYYY/YYYYMMDD.json.gz を読む(収集担当の raw_io があればそれを使う)。"""
    try:
        from src.collect import raw_io  # 収集担当の関数(読むだけ)
        return raw_io.load_raw("openapi", d, raw_dir=raw_dir)
    except ImportError:
        path = raw_dir / "openapi" / f"{d.year}" / f"{d:%Y%m%d}.json.gz"
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)


def odds_dir(d: date, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / "official_odds" / f"{d.year}" / f"{d:%Y%m%d}"


def latest_odds_mtime(d: date, raw_dir: Path = RAW_DIR) -> float:
    """当日の締切前オッズの解析結果(*.json.gz)のうち、最も新しい更新時刻。無ければ 0。"""
    p = odds_dir(d, raw_dir)
    if not p.is_dir():
        return 0.0
    return max((f.stat().st_mtime for f in p.glob("*.json.gz")), default=0.0)


def finish_time(closes: list[datetime], finish_after_minutes: int) -> datetime | None:
    if not closes:
        return None
    return closes[-1] + timedelta(minutes=finish_after_minutes)


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def child_env() -> dict[str, str]:
    """子プロセスの環境変数。activate していなくても LightGBM が動くよう libgomp のパスを足す。"""
    env = dict(os.environ)
    extra = str(VENV_EXTRA_LIB)
    if VENV_EXTRA_LIB.is_dir() and extra not in env.get("LD_LIBRARY_PATH", "").split(":"):
        env["LD_LIBRARY_PATH"] = extra + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def log(msg: str) -> None:
    print(f"[{now_jst():%H:%M:%S}] {msg}", flush=True)


def show(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in ["python", *cmd[1:]])


def run_steps(cmds: list[list[str]], label: str) -> bool:
    """コマンドを順に実行する。途中で失敗したら残りは実行しない(古い入力で export しないため)。"""
    for cmd in cmds:
        log(f"{label}: {show(cmd)}")
        try:
            r = subprocess.run(cmd, cwd=ROOT, env=child_env(), timeout=STEP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            log(f"{label}: タイムアウトしました。以降の手順を飛ばします")
            return False
        if r.returncode != 0:
            log(f"{label}: 終了コード {r.returncode}。以降の手順を飛ばします")
            return False
    return True


def run_odds_export(d: date) -> None:
    """オッズだけの速い更新。--odds-only が未対応(引数エラー=2)なら通常の export_day にする。"""
    cmd = odds_export_command(d)
    log(f"odds: {show(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT, env=child_env(), timeout=STEP_TIMEOUT_SEC,
                       stderr=subprocess.PIPE, text=True)
    if r.returncode == 2 and "--odds-only" in (r.stderr or ""):
        log("odds: export_day が --odds-only に未対応のため、通常の export_day で更新します")
        run_steps([module_cmd("src.export.export_day", "--date", d.isoformat())], "odds")
    elif r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        log(f"odds: 終了コード {r.returncode}")


@dataclass
class Options:
    target: date
    variant: str = "pre"
    with_odds: bool = True
    minutes_before: int = 5
    pages: str | None = None
    stadiums: str | None = None
    only_recommended: bool = False
    race_ids_file: str | None = None
    resume: bool = False
    sleep: float | None = None
    refresh_minutes: int = 30
    finish_after_minutes: int = 40
    skip_prepare: bool = False
    notes: list[str] = field(default_factory=list)


def describe_plan(opt: Options, closes: list[datetime]) -> list[str]:
    """dry-run 用の説明。通信もファイルの書き込みもしない。"""
    lines = [f"対象日: {opt.target.isoformat()}(JST)"]
    if not opt.skip_prepare:
        lines.append("1. 準備:")
        lines += [f"   {show(c)}" for c in prepare_commands(opt.target, opt.variant)]
    if opt.with_odds:
        lines.append("2. 締切前オッズの取得(子プロセス。スケジュールと待機は収集担当の側):")
        ids_note = opt.race_ids_file or ("<推奨買い目のあるレースを書いた一時ファイル>" if opt.only_recommended else None)
        lines.append("   " + show(odds_loop_command(opt.target, opt.minutes_before, opt.pages, opt.stadiums,
                                                    ids_note, opt.sleep, opt.resume)))
        if stopped_marker(opt.target).exists() and not opt.resume:
            lines.append("   ※ この日は取得を止めています(_stopped.json)。確かめてから --resume を付けて起動し直してください")
        lines.append(f"   新しいオッズのファイル({odds_dir(opt.target).relative_to(ROOT)}/*.json.gz)を"
                     f"{TICK_SEC}秒ごとに確認し、増えたら: {show(odds_export_command(opt.target))}")
        if not OFFICIAL_ODDS_MODULE.exists():
            lines.append("   ※ src/collect/official_odds.py がまだ無いため、実行時はこの手順を飛ばします(C-5)")
    lines.append(f"3. 結果反映({opt.refresh_minutes}分ごと):")
    lines += [f"   {show(c)}" for c in refresh_commands(opt.target)]
    if closes:
        fin = finish_time(closes, opt.finish_after_minutes)
        lines.append(f"4. 終了: 締切 {len(closes)}レース(最初 {closes[0]:%H:%M}、最後 {closes[-1]:%H:%M})。"
                     f"{fin:%H:%M} に最後の結果反映をして終わる")
    else:
        lines.append("4. 終了: 当日の出走表がまだ手元に無いため、準備で取得したあとに締切時刻から決める")
    if opt.with_odds and closes:
        n_pages = len((opt.pages or "odds3t,odds2tf,oddstf").split(","))
        lines.append(f"   参考: 締切前オッズのリクエスト数の目安 {len(closes)}レース × {n_pages}ページ = "
                     f"{len(closes) * n_pages}(対象の絞り込み前。spec 07 2.4)")
    return lines


def describe_odds_exit(code: int, stopped: bool) -> str:
    """収集担当の --loop の終了コード(0=正常、1=一部失敗または停止、2=引数エラー)の説明。"""
    if stopped:
        return ("odds: 公式サイトからの拒否か連続失敗で、その日の取得が止まりました(_stopped.json)。"
                "以降は結果反映だけ続けます。原因を確かめてから --skip-prepare --resume で再開してください")
    if code == 0:
        return "odds: 取得ループが正常に終わりました。以降は結果反映だけ続けます"
    if code == 2:
        return "odds: 取得ループの引数が誤っています(終了コード2)。オプションを確かめてください"
    return f"odds: 取得ループが終了しました(終了コード {code}。一部のレースで失敗)。以降は結果反映だけ続けます"


def main_loop(opt: Options) -> int:
    stop = {"flag": False}

    def _stop(signum, _frame):
        log(f"シグナル {signum} を受けたので終了します")
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if not opt.skip_prepare:
        run_steps(prepare_commands(opt.target, opt.variant), "prepare")

    closes = closing_times(load_openapi_raw(opt.target))
    fin = finish_time(closes, opt.finish_after_minutes)
    if fin is None:
        log("当日の締切時刻が取得できません(開催なし、または出走表が未取得)。結果反映を1回して終わります")
        run_steps(refresh_commands(opt.target), "refresh")
        return 0
    log(f"締切 {len(closes)}レース、終了予定 {fin:%H:%M}")

    child: subprocess.Popen | None = None
    temp_ids: str | None = None
    if opt.with_odds:
        ids_file = opt.race_ids_file
        if opt.only_recommended and not ids_file:
            ids = recommended_race_ids(load_day_json(opt.target))
            temp_ids = ids_file = write_race_ids_file(ids)
            log(f"odds: 推奨買い目のあるレース {len(ids)}件だけを取ります")
        if not OFFICIAL_ODDS_MODULE.exists():
            log("odds: src/collect/official_odds.py が無いため、締切前オッズは取りません")
        elif stopped_marker(opt.target).exists() and not opt.resume:
            log("odds: この日は取得を止めています(_stopped.json)。オッズは取らずに結果反映だけ続けます。"
                "確かめてから --skip-prepare --resume で起動し直してください")
        else:
            cmd = odds_loop_command(opt.target, opt.minutes_before, opt.pages, opt.stadiums,
                                    ids_file, opt.sleep, opt.resume)
            log(f"odds: 起動 {show(cmd)}")
            child = subprocess.Popen(cmd, cwd=ROOT, env=child_env())

    last_odds = latest_odds_mtime(opt.target)
    next_refresh = now_jst() + timedelta(minutes=opt.refresh_minutes)
    try:
        while not stop["flag"]:
            if now_jst() >= fin:
                break
            m = latest_odds_mtime(opt.target)
            if m > last_odds:
                last_odds = m
                run_odds_export(opt.target)
            if now_jst() >= next_refresh:
                run_steps(refresh_commands(opt.target), "refresh")
                next_refresh = now_jst() + timedelta(minutes=opt.refresh_minutes)
            if child is not None and child.poll() is not None:
                log(describe_odds_exit(child.returncode, stopped_marker(opt.target).exists()))
                child = None
            time.sleep(TICK_SEC)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
        if temp_ids:
            Path(temp_ids).unlink(missing_ok=True)
    log("最後の結果反映")
    run_steps(refresh_commands(opt.target), "final")
    return 0


def parse_args(argv: list[str] | None) -> tuple[Options, bool, bool]:
    p = argparse.ArgumentParser(description="当日の 収集→予想→export を回す(ローカル実行用)")
    p.add_argument("--date", help="対象日 YYYY-MM-DD(既定: 今日 JST)")
    p.add_argument("--variant", default="pre", choices=["pre", "preview"])
    p.add_argument("--dry-run", action="store_true", help="予定を表示するだけ(通信・書き込みなし)")
    p.add_argument("--prepare-only", action="store_true", help="準備だけ1回実行して終わる")
    p.add_argument("--skip-prepare", action="store_true", help="準備を飛ばす(途中から再開するとき)")
    p.add_argument("--no-odds", action="store_true", help="締切前オッズを取らない")
    p.add_argument("--minutes-before", type=int, default=5, help="締切の何分前にオッズを取るか(3〜15)")
    p.add_argument("--pages", help="取るページ(例: odds3t,odds2tf,oddstf)")
    p.add_argument("--stadiums", help="対象の場(例: 2,12)")
    p.add_argument("--only-recommended", action="store_true",
                   help="推奨買い目のあるレースだけオッズを取る(準備で書き出した JSON から対象を決めて --race-ids-file で渡す)")
    p.add_argument("--race-ids-file", help="オッズを取るレースを絞るファイル(収集担当の CLI にそのまま渡す)")
    p.add_argument("--resume", action="store_true", help="取得を止めた日(_stopped.json)のオッズ取得を再開する")
    p.add_argument("--sleep", type=float, help="オッズ取得のリクエスト間隔(秒。1.0以上。既定は収集担当の既定)")
    p.add_argument("--refresh-minutes", type=int, default=30)
    p.add_argument("--finish-after-minutes", type=int, default=40)
    a = p.parse_args(argv)
    if not 3 <= a.minutes_before <= 15:
        p.error("--minutes-before は 3〜15 で指定してください")
    if a.sleep is not None and a.sleep < 1.0:
        p.error("--sleep は 1.0 以上にしてください(CLAUDE.md の方針)")
    if a.refresh_minutes < 10:
        p.error("--refresh-minutes は 10 以上にしてください(openapi の更新間隔が約10分のため)")
    target = date.fromisoformat(a.date) if a.date else now_jst().date()
    opt = Options(
        target=target, variant=a.variant, with_odds=not a.no_odds, minutes_before=a.minutes_before,
        pages=a.pages, stadiums=a.stadiums, only_recommended=a.only_recommended,
        race_ids_file=a.race_ids_file, resume=a.resume, sleep=a.sleep,
        refresh_minutes=a.refresh_minutes, finish_after_minutes=a.finish_after_minutes,
        skip_prepare=a.skip_prepare,
    )
    return opt, a.dry_run, a.prepare_only


def main(argv: list[str] | None = None) -> int:
    opt, dry_run, prepare_only = parse_args(argv)
    if dry_run:
        for line in describe_plan(opt, closing_times(load_openapi_raw(opt.target))):
            print(line)
        return 0
    if prepare_only:
        return 0 if run_steps(prepare_commands(opt.target, opt.variant), "prepare") else 1
    return main_loop(opt)


if __name__ == "__main__":
    sys.exit(main())
