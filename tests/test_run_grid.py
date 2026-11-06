"""The grid runner: resumable, stops cleanly on quota and on a deadline, writes a manifest."""
import importlib.util
import json
from pathlib import Path

import pytest

from dst.llm import FakeClient, QuotaExhausted

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("run_grid", ROOT / "scripts" / "run_grid.py")
run_grid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_grid)

ARGS = ["--model", "fake", "--shapes", "F", "--conditions", "B0,E3", "--seeds", "1-2", "--config", "/nonexistent"]


def test_parse_seeds():
    assert run_grid.parse_seeds("1-3,5,2") == [1, 2, 3, 5]


def test_grid_runs_resumes_and_writes_manifest(tmp_path, capsys):
    out = tmp_path / "g"
    assert run_grid.main(ARGS + ["--out", str(out)]) == 0
    man = json.loads((out / "grid_manifest.json").read_text())
    assert man["episodes_total"] == 4 and man["episodes_done"] == 4 and man["episodes_left"] == 0
    assert set(man["episodes"]) == {"F-B0-s1", "F-E3-s1", "F-B0-s2", "F-E3-s2"}
    assert all(v["status"] == "done" and v["llm_calls"] > 0 for v in man["episodes"].values())
    first = (out / "F-B0-s1" / "manifest.json").read_text()
    # a second invocation skips every finished episode
    assert run_grid.main(ARGS + ["--out", str(out)]) == 0
    assert (out / "F-B0-s1" / "manifest.json").read_text() == first
    man2 = json.loads((out / "grid_manifest.json").read_text())
    assert len(man2["invocations"]) == 2 and man2["invocations"][-1]["ran"] == 0


def test_quota_stops_the_grid_and_the_rerun_completes(tmp_path, monkeypatch):
    out = tmp_path / "g"
    calls = {"n": 0}

    class Limited(FakeClient):
        def complete(self, messages, **kw):
            calls["n"] += 1
            if calls["n"] > 50:
                raise QuotaExhausted("free daily quota reached")
            return super().complete(messages, **kw)

    monkeypatch.setattr(run_grid, "make_client", lambda provider, key: Limited())
    rc = run_grid.main(ARGS + ["--out", str(out), "--parallel", "1"])
    assert rc == 3
    man = json.loads((out / "grid_manifest.json").read_text())
    assert man["episodes_left"] >= 1 and man["invocations"][-1]["stop_reason"] == "aborted_quota"
    statuses = [v["status"] for v in man["episodes"].values()]
    assert "aborted" in statuses and statuses.count("missing") >= 1     # nothing new started after the stop
    aborted = [k for k, v in man["episodes"].items() if v["status"] == "aborted"]
    assert json.loads((out / aborted[0] / "manifest.json").read_text())["end_reason"] == "aborted_quota"
    calls["n"] = -10_000                                                  # the quota "resets"
    assert run_grid.main(ARGS + ["--out", str(out), "--parallel", "1"]) == 0
    man = json.loads((out / "grid_manifest.json").read_text())
    assert man["episodes_done"] == 4


def test_deadline_stops_cleanly(tmp_path, monkeypatch):
    out = tmp_path / "g"
    import time

    class Slow(FakeClient):
        def complete(self, messages, **kw):
            time.sleep(0.05)
            return super().complete(messages, **kw)

    monkeypatch.setattr(run_grid, "make_client", lambda provider, key: Slow())
    rc = run_grid.main(ARGS + ["--out", str(out), "--parallel", "2", "--deadline-min", str(1.2 / 60)])
    assert rc == 3
    man = json.loads((out / "grid_manifest.json").read_text())
    assert man["invocations"][-1]["stop_reason"] == "aborted_deadline"
    for k, v in man["episodes"].items():
        if v["status"] == "aborted":
            assert v["end_reason"] == "aborted_deadline"


def test_dry_run_calls_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(run_grid, "make_client", lambda *a: pytest.fail("no client in a dry run"))
    assert run_grid.main(ARGS + ["--out", str(tmp_path / "g"), "--dry-run"]) == 0
    assert capsys.readouterr().out.count("would run") == 4


def test_pilot_table_on_a_grid(tmp_path, capsys):
    out = tmp_path / "g"
    assert run_grid.main(ARGS + ["--out", str(out)]) == 0
    spec2 = importlib.util.spec_from_file_location("pilot_table", ROOT / "scripts" / "pilot_table.py")
    pt = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(pt)
    capsys.readouterr()
    assert pt.main([str(out), "--md"]) == 0
    text = capsys.readouterr().out
    assert "4 finished episodes" in text and "| B0 | 2 |" in text and "| E3 | 2 |" in text


def test_frozen_config_is_read(tmp_path, capsys):
    cfg = ROOT / "configs" / "frozen.json"
    frozen = json.loads(cfg.read_text())
    assert run_grid.main(["--model", "fake", "--shapes", "F", "--conditions", "B0", "--seeds", "1",
                          "--out", str(tmp_path / "g"), "--dry-run"]) == 0
    assert f"'p_timeout': {frozen['faults']['p_timeout']}" in capsys.readouterr().out
