"""Tests for the figure, replay-check, audit and secret-scan tools."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.figures import make_all
from analysis.report import analyze
from analysis.scripted_demo import generate
from analysis.secretscan import load_words, scan_repo, scan_text

ROOT = Path(__file__).resolve().parent.parent


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    d = tmp_path_factory.mktemp("runs")
    generate(d, seeds=range(1, 3), agents=("scripted-naive", "scripted-careful"), shapes=("F", "D"))
    return d


def test_figures_are_written(runs, tmp_path):
    analyze([runs], tmp_path / "res", n_boot=200, n_perm=200, label="test")
    paths = make_all(tmp_path / "res", tmp_path / "fig")
    names = sorted(p.name for p in paths)
    for stem in ("fig1_failures_by_condition", "fig2_cost_vs_reduction", "fig3_failures_by_shape",
                 "fig_validation_scripted"):
        assert f"{stem}.pdf" in names and f"{stem}.png" in names
    assert all(p.stat().st_size > 5000 for p in paths)


def test_replay_check_passes_on_scripted_runs(runs, capsys):
    assert _script("replay_check").main([str(runs), "--n", "2", "--seed", "0"]) == 0
    out = capsys.readouterr().out
    assert out.count("IDENTICAL") == 2 and "all replays byte-identical" in out


def test_audit_sample_writes_digests_and_table(runs, tmp_path, capsys):
    out = tmp_path / "audit"
    assert _script("audit_sample").main([str(runs), "--n", "3", "--seed", "5", "--out", str(out)]) == 0
    digests = sorted(p for p in out.glob("*.md") if p.name != "AUDIT.md")
    assert len(digests) == 3
    table = (out / "AUDIT.md").read_text()
    n_labels = 0
    for p in digests:
        text = p.read_text()
        assert "## Timeline" in text and "## Ledger effects" in text and "## Computed labels" in text
        n_labels += sum(1 for line in text.splitlines() if line.startswith(("| DUP", "| FD", "| PD")))
        assert f"[{p.stem}]({p.name}) | missed" in table
    rows = [line for line in table.splitlines() if line.startswith("| [")]
    assert len(rows) == n_labels + 3
    # same seed, same sample
    out2 = tmp_path / "audit2"
    _script("audit_sample").main([str(runs), "--n", "3", "--seed", "5", "--out", str(out2)])
    assert (out / "AUDIT.md").read_text().replace(str(out), "") == (out2 / "AUDIT.md").read_text().replace(str(out2), "")


# --- secret scan --------------------------------------------------------------------------

FAKE_GOOGLE = "AI" + "za" + "Sy" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7"  # built at runtime: not a literal key
FAKE_GH = "gh" + "p_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3hJ5"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def test_scan_text_patterns():
    assert scan_text(f"key = {FAKE_GOOGLE}") == ["google-api-key"]
    assert "github-classic-token" in scan_text(FAKE_GH)
    assert "secrets-path" in scan_text("cat ~/app/." + "secrets/token")
    assert scan_text("Authorization: Bearer " + "x7Qp2LmZ9vR4tY8wK3nB6cJ1") == ["generic-bearer-token"]
    assert scan_text('api_key = "<your-key-goes-here-please>"') == []  # placeholder, low entropy
    assert scan_text("the lead delegates to workers") == []


def test_secret_scan_finds_history_and_never_prints_values(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("hello\n")
    (repo / "conf.py").write_text(f"KEY = '{FAKE_GOOGLE}'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add config")
    (repo / "conf.py").write_text("KEY = None\n")  # removed later: still in history
    (repo / "notes.md").write_text("we tested the Zephyrine pipeline\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "remove key; mention zephyrine")
    words = tmp_path / "words.txt"  # outside the repo
    words.write_text("# private list\nzephyrine\n")
    hits = scan_repo(repo, load_words(words))
    where = {(w.split(":")[-1] if ":" in w else w, name) for w, _, name in hits}
    assert ("conf.py", "google-api-key") in where           # from history only
    assert ("notes.md", "forbidden-word#2") in where       # working tree
    assert ("COMMIT_MSG", "forbidden-word#2") in where     # commit message
    assert not any(n == "google-api-key" and ":" not in w for w, _, n in hits)  # not in the current tree

    mod = _script("secret_scan")
    assert mod.main(["--repo", str(repo), "--words", str(words)]) == 1
    out = capsys.readouterr().out
    assert FAKE_GOOGLE not in out and "zephyrine" not in out.lower() and "HIT" in out
    assert mod.main(["--repo", str(repo), "--no-history"]) == 0  # current tree is clean without the word list


def test_words_file_inside_repo_must_be_untracked(tmp_path, capsys):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "w.txt").write_text("secretword\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "x")
    assert _script("secret_scan").main(["--repo", str(repo), "--words", str(repo / "w.txt")]) == 2


def test_this_repository_is_clean():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    hits = scan_repo(ROOT, history=True)
    assert hits == [], [(w, line, n) for w, line, n in hits]
