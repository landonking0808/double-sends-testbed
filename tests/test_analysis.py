"""Tests for the analysis pipeline on synthetic episodes from the scripted agents."""
import csv
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from analysis import stats
from analysis.load import load_rows
from analysis.report import analyze
from analysis.scripted_demo import generate
from dst.metrics import wilson

ROOT = Path(__file__).resolve().parent.parent
FAST = {"n_boot": 400, "n_perm": 400}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    d = tmp_path_factory.mktemp("runs")
    generate(d / "s", seeds=range(1, 5), agents=("scripted-naive",), shapes=("F", "D"))
    generate(d / "s", seeds=range(1, 3), agents=("scripted-careful",), shapes=("F",), conditions=("B0", "E3"))
    # one episode through the documented command line
    subprocess.run([sys.executable, "-m", "dst.run", "run", "--shape", "F", "--condition", "B0", "--agents",
                    "scripted-naive", "--seed", "9", "--out", str(d / "s" / "cli" / "F-B0-naive-s9")],
                   cwd=ROOT, check=True, capture_output=True)
    return d / "s"


@pytest.fixture(scope="module")
def result(runs, tmp_path_factory):
    out = tmp_path_factory.mktemp("res")
    return out, analyze([runs], out, **FAST)


def _csv(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def test_outputs_exist_and_every_episode_is_a_row(runs, result):
    out, res = result
    for f in ("episodes.csv", "table1.csv", "table1.md", "tests.csv", "excluded.csv", "validation.csv", "summary.json"):
        assert (out / f).stat().st_size > 0, f
    n_dirs = sum(1 for _ in runs.rglob("ledger.json"))
    eps = _csv(out / "episodes.csv")
    assert len(eps) == n_dirs == 2 * 5 * 4 + 2 * 2 + 1
    assert all(r["included"] == "1" and r["excluded_reason"] == "" for r in eps)


def test_analysis_is_deterministic(runs, result, tmp_path):
    out, _ = result
    analyze([runs], tmp_path, **FAST)
    for f in ("episodes.csv", "table1.csv", "table1.md", "tests.csv", "validation.csv", "summary.json"):
        assert (out / f).read_bytes() == (tmp_path / f).read_bytes(), f


def test_table_numbers_come_from_the_episode_rows(result):
    out, _ = result
    eps = [r for r in _csv(out / "episodes.csv") if r["model"] == "scripted-naive" and r["shape"] == "F"
           and r["condition"] == "B0"]
    t1 = {(r["model"], r["shape"], r["condition"]): r for r in _csv(out / "table1.csv")}
    row = t1[("scripted-naive", "F", "B0")]
    assert int(row["n_episodes"]) == len(eps) == 5  # seeds 1-4 plus the CLI episode
    extras = sum(int(r["dar_extras"]) for r in eps)
    req = sum(int(r["dar_required"]) for r in eps)
    assert float(row["dar_per100"]) == pytest.approx(100 * extras / req, rel=1e-5)
    assert float(row["dar_per100_lo"]) <= float(row["dar_per100"]) <= float(row["dar_per100_hi"])
    k = sum(int(r["fcr_worker_k"]) for r in eps)
    n = sum(int(r["fcr_worker_n"]) for r in eps)
    lo, hi = wilson(k, n)
    assert (int(row["fcr_worker_k"]), int(row["fcr_worker_n"])) == (k, n)
    assert float(row["fcr_worker_lo"]) == pytest.approx(lo, rel=1e-5, abs=1e-9)
    assert float(row["fcr_worker_hi"]) == pytest.approx(hi, rel=1e-5)
    # the shape-pooled row adds the shapes
    allrow = t1[("scripted-naive", "all", "B0")]
    assert int(allrow["n_episodes"]) == 5 + 4


def test_overhead_and_controls(result):
    out, _ = result
    t1 = {(r["model"], r["shape"], r["condition"]): r for r in _csv(out / "table1.csv")}
    assert float(t1[("scripted-naive", "all", "B0")]["steps_overhead_vs_b0"]) == 0
    # idempotency removes every duplicate of the naive retrier
    assert float(t1[("scripted-naive", "all", "E1")]["dar_per100"]) == 0
    assert float(t1[("scripted-naive", "all", "E1")]["dar_per100_reduction_vs_b0"]) > 30
    # the careful team has no failures anywhere
    assert float(t1[("scripted-careful", "F", "E3")]["failure_episodes"]) == 0


def test_baseline_tests_and_holm(result):
    out, _ = result
    ts = _csv(out / "tests.csv")
    by = {(r["model"], r["shape"], r["outcome"], r["control"]): r for r in ts}
    e1 = by[("scripted-naive", "all", "any_duplicate", "E1")]
    assert e1["test"] == "fisher_exact" and float(e1["p_holm"]) < 0.05 and e1["reject_holm_0.05"] == "1"
    # scripted agents ignore the prompt: P1 equals B0 exactly (shape D: same seeds in both cells)
    p1 = by[("scripted-naive", "D", "dar_per100", "P1")]
    assert float(p1["effect"]) == 0 and float(p1["p"]) == 1
    for r in ts:
        if r["p"]:
            assert float(r["p_holm"]) >= float(r["p"]) - 1e-12
    # one family = one (model, faults, shape, outcome); 4 controls for the naive team
    assert e1["holm_family_size"] == "4"


def test_scripted_validation_matches_closed_form(result):
    out, _ = result
    val = _csv(out / "validation.csv")
    naive = [r for r in val if r["agents"] == "scripted-naive" and r["condition"] in ("B0", "P1", "E2")]
    assert naive and all(abs(float(r["dar_z"])) <= 4 for r in naive)
    assert all(float(r["dar_analytic_per100"]) == 0 and float(r["dar_measured_per100"]) == 0
               for r in val if r["condition"] in ("E1", "E3"))


def test_excluded_episodes_are_reported_not_dropped(runs, tmp_path):
    src = sorted(runs.rglob("ledger.json"))[0].parent
    work = tmp_path / "runs"
    shutil.copytree(runs, work)
    rel = src.relative_to(runs)
    # 1) tampered ledger -> hash mismatch
    led = work / rel / "ledger.json"
    led.write_text(led.read_text().replace('"quiescent"', '"max_ticks"'))
    # 2) a replay copy
    rp = work / "replays" / "x-replay"
    shutil.copytree(runs / rel, rp)
    m = json.loads((rp / "manifest.json").read_text())
    m["replay_of"] = str(src)
    (rp / "manifest.json").write_text(json.dumps(m))
    # 3) aborted run, 4) a directory with a missing log
    ab = work / "aborted" / "a1"
    shutil.copytree(runs / rel, ab)
    m = json.loads((ab / "manifest.json").read_text())
    m["aborted"] = True
    (ab / "manifest.json").write_text(json.dumps(m))
    br = work / "broken" / "b1"
    shutil.copytree(runs / rel, br)
    (br / "scheduler.jsonl").unlink()
    # 5) an episode that ended "aborted_quota" (no manifest flag, hashes not checked without one)
    aq = work / "aborted" / "a2"
    shutil.copytree(runs / rel, aq)
    led = json.loads((aq / "ledger.json").read_text())
    led["end"]["reason"] = "aborted_quota"
    (aq / "ledger.json").write_text(json.dumps(led))
    m = json.loads((aq / "manifest.json").read_text())
    m.pop("files_sha256")
    (aq / "manifest.json").write_text(json.dumps(m))
    res = analyze([work], tmp_path / "out", **FAST)
    reasons = sorted(r["excluded_reason"].split(":")[0] for r in res["rows"] if not r["included"])
    assert reasons == ["aborted", "aborted", "hash_mismatch", "replay_copy", "unreadable"]
    excl = _csv(tmp_path / "out" / "excluded.csv")
    assert len(excl) == 5
    assert "excluded: 5" in (tmp_path / "out" / "table1.md").read_text()
    assert res["summary"]["episodes_included"] == res["summary"]["episodes_found"] - 5


def test_load_rows_counts_match_metrics(runs):
    rows, metrics, ledgers = load_rows([runs])
    assert len(metrics) == len(ledgers) == len(rows)
    r = rows[0]
    m = metrics[r["run_dir"]]
    assert r["dar_extras"] == m["dar"]["extras"] and r["steps"] == m["cost"]["steps"]


# --- statistics ------------------------------------------------------------------------

def test_holm_known_values():
    adj = stats.holm([0.01, 0.04, 0.03, float("nan")])
    assert adj[0] == pytest.approx(0.03) and adj[2] == pytest.approx(0.06) and adj[1] == pytest.approx(0.06)
    assert math.isnan(adj[3])


def test_fisher_and_permutation():
    f = stats.fisher_exact(10, 10, 0, 10)
    assert f["p"] == pytest.approx(1.0825088224469026e-05)
    same = stats.permutation_test([1, 2, 3], [1, 2, 3], n_perm=200, seed=1)
    assert same["diff"] == 0 and same["p"] > 0.9
    a = stats.permutation_test([0] * 8, [5] * 8, n_perm=500, seed=3)
    b = stats.permutation_test([0] * 8, [5] * 8, n_perm=500, seed=3)
    assert a == b and a["p"] < 0.01
    st = stats.permutation_test([0, 0, 10, 10], [1, 1, 11, 11], ["F", "F", "D", "D"], ["F", "F", "D", "D"],
                                n_perm=500, seed=0)
    assert st["diff"] == 1 and st["p"] < 0.1  # within-shape shift is detected despite the large shape effect


def test_bootstrap_is_seeded():
    num, den = {1: 3, 2: 5, 3: 0, 4: 2}, {1: 20, 2: 20, 3: 20, 4: 20}
    a = stats.cluster_bootstrap_ratio(num, den, n_boot=300, seed=7, scale=100)
    b = stats.cluster_bootstrap_ratio(num, den, n_boot=300, seed=7, scale=100)
    assert a == b and a[0] <= 12.5 <= a[1]
    assert stats.sub_seed(0, "x", "F") == stats.sub_seed(0, "x", "F") != stats.sub_seed(0, "x", "D")
