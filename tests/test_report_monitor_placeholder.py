"""The report build's monitor placeholder ({{M:...}}) reads run_monitor.py's summary.csv."""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytest.importorskip("docx")
pytest.importorskip("lxml")


def _build():
    spec = importlib.util.spec_from_file_location("report_build", ROOT / "report" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["report_build"] = mod
    spec.loader.exec_module(mod)
    return mod


FIELDS = ["monitor", "view", "group", "metric", "k", "n", "value", "ci95_lo", "ci95_hi", "ci_kind"]


def _write_summary(path: Path, episodes: int, view="report"):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"monitor": "llm", "view": view, "group": "all", "metric": "episodes", "k": episodes},
        {"monitor": "llm", "view": view, "group": "all", "metric": "dup_recall", "k": 13, "n": 22,
         "value": 0.5909, "ci95_lo": 0.3873, "ci95_hi": 0.7674, "ci_kind": "wilson"},
        {"monitor": "llm", "view": view, "group": "all", "metric": "fd_f1", "n": episodes,
         "value": 0.25, "ci95_lo": 0.0, "ci95_hi": 0.5, "ci_kind": "episode_bootstrap"},
        {"monitor": "llm", "view": view, "group": "team_model=gemma-x", "metric": "dup_recall", "k": 3,
         "n": 4, "value": 0.75, "ci95_lo": 0.3, "ci95_hi": 0.95, "ci_kind": "wilson"},
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _tables(B, tmp_path, monkeypatch, episodes_expected=2):
    monkeypatch.setattr(B, "REPO", tmp_path)
    cfg = {"grids": {"g": {"models": {"A": "gemma-x"}}},
           "monitor_views": {"report": {"file": "monitor/g-report/summary.csv", "episodes": episodes_expected},
                             "merged": {"file": "monitor/g-summary.csv", "view": "report",
                                        "episodes": episodes_expected}}}
    return B.Tables("g", cfg)


def test_monitor_placeholder_resolves_and_logs(tmp_path, monkeypatch):
    B = _build()
    _write_summary(tmp_path / "results/monitor/g-report/summary.csv", episodes=2)
    _write_summary(tmp_path / "results/monitor/g-summary.csv", episodes=2)
    t = _tables(B, tmp_path, monkeypatch)
    text = ("{{M:report:llm:all:dup_recall}} {{M:report:llm:all:dup_recall:full}} {{M:report:llm:all:fd_f1:ci}} "
            "{{M:report:llm:team_model=A:dup_recall:kn}} {{M:merged:llm:all:episodes}} "
            "{{M:report:llm:all:dup_recall|pct1}}")
    out = B.resolve_placeholders(text, t)
    assert out == "59% 59% [39, 77] (13/22) 0.25 [0.00, 0.50] 3/4 2 59.1%"
    assert len(t.log) == 6 and all(r["file"].startswith("results/monitor/") and r["sha256"] for r in t.log)
    assert not t.pending


def test_monitor_placeholder_pending_until_run_finished(tmp_path, monkeypatch):
    B = _build()
    t = _tables(B, tmp_path, monkeypatch, episodes_expected=160)
    assert B.resolve_placeholders("{{M:report:llm:all:dup_recall}}", t) == "[monitor pending]"   # no file
    _write_summary(tmp_path / "results/monitor/g-report/summary.csv", episodes=25)
    assert B.resolve_placeholders("{{M:report:llm:all:dup_recall}}", t) == "[monitor pending]"   # 25/160
    assert len(t.pending) == 2 and t.log[-1]["raw"] == "pending"


def test_monitor_placeholder_errors(tmp_path, monkeypatch):
    B = _build()
    _write_summary(tmp_path / "results/monitor/g-report/summary.csv", episodes=2)
    t = _tables(B, tmp_path, monkeypatch)
    for bad in ("{{M:full:llm:all:dup_recall}}", "{{M:report:llm:all:no_such_metric}}",
                "{{M:report:llm:all:dup_recall:bogus}}", "{{M:report:llm:all:episodes:ci}}"):
        with pytest.raises(B.BuildError):
            B.resolve_placeholders(bad, t)
