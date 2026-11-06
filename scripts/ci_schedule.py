"""GitHub Actions 用: 当日のレース一覧と締切時刻を R2 の schedule/YYYYMMDD.json に置く(CI-5。spec/12 1.3)。

Cloudflare Worker「beforeinfo」がこの小さなファイル(約12KB)だけを読み、締切の15〜10分前のレースの
直前情報を取りに行く。中止・順延(is_canceled)と締切時刻の無いレースは入れない。

入力は当日の interim の races(build_interim の後に呼ぶ)。通信は R2 だけ(公式サイト・Open API には触らない)。
締切時刻が変わったときだけ上書きする(R2 の今のファイルを取得して races を比べる。generated_at は比べない)。

使い方:
    python -m scripts.ci_schedule --date 2026-11-06            # 変わっていれば R2 に書く
    python -m scripts.ci_schedule --date 2026-11-06 --dry-run  # 内容とサイズを表示するだけ(R2 に触らない)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from scripts.ci_state import BUCKET, JST, today_jst, wrangler_cmd


def schedule_key(d: date) -> str:
    return f"{BUCKET}/schedule/{d.strftime('%Y%m%d')}.json"


def build_schedule(races, d: date, now: datetime) -> dict:
    """interim の races(当日分)から schedule の dict を作る。"""
    import pandas as pd

    df = races[races["date"].astype(str) == d.isoformat()] if "date" in races else races
    canceled = df["is_canceled"].fillna(False).astype(bool) if "is_canceled" in df else False
    df = df[~canceled & df["closed_at"].notna()]
    out = []
    for r in df.sort_values(["closed_at", "stadium_number", "race_number"]).itertuples():
        closed = pd.Timestamp(r.closed_at)
        closed = closed.tz_localize(JST) if closed.tzinfo is None else closed.tz_convert(JST)
        out.append({"race_id": str(r.race_id), "jcd": int(r.stadium_number), "rno": int(r.race_number),
                    "closed_at": closed.isoformat()})
    return {"date": d.isoformat(), "generated_at": now.astimezone(JST).replace(microsecond=0).isoformat(),
            "races": out}


def same_races(a: dict | None, b: dict) -> bool:
    return a is not None and a.get("date") == b["date"] and a.get("races") == b["races"]


def load_races(d: date):
    from src.features.build_interim import read_interim  # 特徴量担当の関数(読むだけ)
    return read_interim("races", d, d)


def fetch_current(d: date, tmp: Path) -> dict | None:
    """R2 の今の schedule。無い・読めないときは None(その場合は書く)。"""
    f = tmp / "current.json"
    try:
        subprocess.run(wrangler_cmd("get", schedule_key(d), f), check=True, cwd=tempfile.gettempdir(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return json.loads(f.read_text(encoding="utf-8"))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="R2 の schedule/YYYYMMDD.json を書く")
    p.add_argument("--date", help="YYYY-MM-DD(既定: 今日 JST)")
    p.add_argument("--dry-run", action="store_true", help="R2 に触らない")
    a = p.parse_args(argv)
    d = date.fromisoformat(a.date) if a.date else today_jst()
    sched = build_schedule(load_races(d), d, datetime.now(JST))
    text = json.dumps(sched, ensure_ascii=False, separators=(",", ":"))
    print(f"[schedule] {d}: {len(sched['races'])} レース、{len(text.encode())} バイト")
    if not sched["races"]:
        print("[schedule] 対象のレースが無いので書きません")
        return 0
    if a.dry_run:
        print("  $ " + " ".join(wrangler_cmd("put", schedule_key(d), Path("schedule.json"))))
        print("[schedule] dry-run: R2 には触っていません")
        return 0
    with tempfile.TemporaryDirectory(prefix="ci_schedule_") as tmp:
        if same_races(fetch_current(d, Path(tmp)), sched):
            print("[schedule] 締切時刻に変更が無いので書きません")
            return 0
        f = Path(tmp) / "schedule.json"
        f.write_text(text, encoding="utf-8")
        try:
            subprocess.run(wrangler_cmd("put", schedule_key(d), f), check=True, cwd=tempfile.gettempdir())
        except subprocess.CalledProcessError as e:
            print(f"[schedule] 書き込みに失敗しました: {e}", file=sys.stderr)
            return 1
    print("[schedule] 書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
