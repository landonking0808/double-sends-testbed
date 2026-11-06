import json

import pytest

from dst.run import main


@pytest.mark.parametrize("agents", ["scripted-naive", "scripted-careful"])
@pytest.mark.parametrize("shape", ["F", "D"])
@pytest.mark.parametrize("condition", ["B0", "P1", "E1", "E2", "E3"])
def test_run_every_condition_and_shape(tmp_path, capsys, agents, shape, condition):
    out = tmp_path / "ep"
    assert main(["run", "--shape", shape, "--condition", condition, "--agents", agents, "--seed", "1",
                 "--out", str(out)]) == 0
    for name in ("episode.jsonl", "scheduler.jsonl", "ledger.json", "manifest.json", "metrics.json"):
        assert (out / name).stat().st_size > 0
    man = json.loads((out / "manifest.json").read_text())
    assert man["condition"] == condition and man["shape"] == shape and man["faults"]["p_timeout"] == 0.2
    assert "git_commit" in man and set(man["files_sha256"]) == {"episode.jsonl", "scheduler.jsonl", "ledger.json"}
    led = json.loads((out / "ledger.json").read_text())
    assert led["end"]["reason"] == "quiescent"
    lines = (out / "episode.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert set(first) == {"episode_id", "seed", "t", "agent", "parent", "event", "payload", "ledger_ref"}


@pytest.mark.parametrize("condition,shape", [("B0", "F"), ("E3", "D"), ("E1", "D+")])
def test_rerun_replay_is_byte_identical(tmp_path, capsys, condition, shape):
    out = tmp_path / "ep"
    assert main(["run", "--shape", shape, "--condition", condition, "--agents", "scripted-naive", "--seed", "5",
                 "--out", str(out)]) == 0
    assert main(["rerun", "--replay", str(out), "--out", str(tmp_path / "rep")]) == 0
    assert "replay identical" in capsys.readouterr().out
    for name in ("episode.jsonl", "scheduler.jsonl", "ledger.json"):
        assert (out / name).read_bytes() == (tmp_path / "rep" / name).read_bytes()


def test_metrics_subcommand(tmp_path, capsys):
    for s in (1, 2):
        main(["run", "--shape", "F", "--condition", "B0", "--seed", str(s), "--out", str(tmp_path / f"e{s}")])
    assert main(["metrics", str(tmp_path / "e1"), str(tmp_path / "e2")]) == 0
    out = capsys.readouterr().out
    assert '"episodes": 2' in out and "dar_per100" in out


def test_llm_path_needs_provider_and_imports_lazily():
    import subprocess
    import sys
    with pytest.raises(ValueError):
        main(["run", "--agents", "llm", "--model", "m", "--out", "/tmp/never-written-dst"])
    # importing the runner (and running scripted episodes) never imports the LLM layer
    code = ("import sys; from dst.run import run_episode; run_episode(seed=1); "
            "print('dst.llm' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False", out.stderr
