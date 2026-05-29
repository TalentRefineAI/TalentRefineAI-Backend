"""Evidencias estables para la suite unitaria."""

from __future__ import annotations

import datetime
import json
import platform
from pathlib import Path
from typing import Any

UNIT_LOG_DIR: Path | None = None
UNIT_RESULTS: list[dict[str, Any]] = []


def pytest_configure(config) -> None:
    global UNIT_LOG_DIR, UNIT_RESULTS
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    log_dir = Path(__file__).resolve().parent / "logs" / f"unit_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    UNIT_LOG_DIR = log_dir
    UNIT_RESULTS = []
    (log_dir / "test_start.txt").write_text(
        f"Starting unit test run at {timestamp}\n",
        encoding="utf-8",
    )


def pytest_runtest_logreport(report) -> None:
    if report.when != "call":
        return
    if UNIT_LOG_DIR is None:
        return
    UNIT_RESULTS.append(
        {
            "nodeid": report.nodeid,
            "outcome": report.outcome,
            "duration": report.duration,
        }
    )


def pytest_sessionfinish(session, exitstatus: int) -> None:
    log_dir = UNIT_LOG_DIR
    results = UNIT_RESULTS
    if log_dir is None:
        return

    counts: dict[str, int] = {}
    for item in results:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1

    summary = {
        "exitstatus": exitstatus,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "total": len(results),
        "counts": counts,
        "results": results,
    }
    (log_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"exitstatus={exitstatus}",
        f"total={len(results)}",
        *(f"{name}={count}" for name, count in sorted(counts.items())),
        "",
        "tests:",
        *(f"- {item['outcome']} {item['nodeid']} ({item['duration']:.4f}s)" for item in results),
        "",
    ]
    (log_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    (log_dir / "test_end.txt").write_text(
        f"Finished unit test run with exitstatus={exitstatus}\n",
        encoding="utf-8",
    )
