"""GitHub Actions 用: 成功したジョブの最後に docs/data/status.json を書く(spec/10 1.1)。

Cloudflare への公開に含める(git にはコミットしない)。失敗したジョブでは呼ばない
(前回の成功時刻のまま残る)。URL や実行IDなどの内部の情報は入れない。

使い方:
    python -m scripts.ci_status --job daytime            # docs/data/status.json を書く
    python -m scripts.ci_status --job morning --dry-run  # 書かずに内容を表示する
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
OUT_PATH = ROOT / "docs" / "data" / "status.json"
MODELS_DIR = ROOT / "models"
MODEL_VARIANTS = ("pre", "preview")
# ci_run.sh のモード名 → status.json の job 名
JOB_NAMES = {"morning": "morning", "daytime": "update", "finalize": "finalize", "monthly": "monthly"}


def schedule_times(day: datetime) -> list[datetime]:
    """その日(JST)の定期実行の予定時刻(.github/workflows/daily.yml の cron と合わせる)。"""
    d = day.date()
    times = [time(1, 12), time(7, 41)]  # 前日分の確定、朝
    for h in range(8, 24):              # 日中: 8:04〜23:34 の30分ごと
        times += [time(h, 4), time(h, 34)]
    return sorted(datetime.combine(d, t, JST) for t in times)


def next_expected(now: datetime) -> datetime:
    """now より後の、最初の定期実行の予定時刻。"""
    now = now.astimezone(JST)
    for day in (now, now + timedelta(days=1)):
        for t in schedule_times(day):
            if t > now:
                return t
    raise AssertionError("unreachable")


def read_model_ids(models_dir: Path = MODELS_DIR) -> dict[str, str]:
    """latest に書かれたモデルID。pre は models/latest、他は models/<variant>/latest
    (src/model/lgbm.py と同じ規則)。無い変種は入れない。"""
    out = {}
    for v in MODEL_VARIANTS:
        p = models_dir / "latest" if v == "pre" else models_dir / v / "latest"
        if p.is_file():
            mid = p.read_text(encoding="utf-8").strip()
            if mid:
                out[v] = mid
    return out


def build_status(job: str, now: datetime, models_dir: Path = MODELS_DIR) -> dict:
    now = now.astimezone(JST).replace(microsecond=0)
    return {
        "schema_version": 1,
        "last_success_at": now.isoformat(),
        "job": JOB_NAMES[job],
        "next_expected_by": next_expected(now).isoformat(),
        "models": read_model_ids(models_dir),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="docs/data/status.json を書く")
    p.add_argument("--job", required=True, choices=list(JOB_NAMES))
    p.add_argument("--now", help="時刻の指定(ISO 8601。テスト用)")
    p.add_argument("--dry-run", action="store_true", help="書かずに表示する")
    a = p.parse_args(argv)
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    text = json.dumps(build_status(a.job, now), ensure_ascii=False, indent=2) + "\n"
    if a.dry_run:
        print(text, end="")
        return 0
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"{OUT_PATH.relative_to(ROOT)} を書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
