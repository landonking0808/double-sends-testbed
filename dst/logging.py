"""Episode logging: one JSONL event log, a scheduler log, the ledger and a run manifest.

Files in an episode directory (see docs/LOG_SCHEMA.md):

* ``episode.jsonl``   - one line per event: ``episode_id, seed, t, agent, parent, event, payload, ledger_ref``
* ``scheduler.jsonl`` - one line per tick: the seeded turn order and every agent's status
* ``ledger.json``     - the environment-owned ground truth (effects, reports, delegations, ...)
* ``manifest.json``   - run settings, code commit, file hashes

Serialization is deterministic (sorted keys, fixed separators, no wall-clock time in
the event log), so a replayed episode is byte-identical to the original.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

EVENTS = ("llm_call", "tool_call", "tool_result", "message", "report")
LOG_FILES = ("episode.jsonl", "scheduler.jsonl", "ledger.json")


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class MemoryLogger:
    """Collects the serialized lines in memory (used by tests and sweeps)."""

    def __init__(self, episode_id: str, seed: int):
        self.episode_id = episode_id
        self.seed = seed
        self.event_lines: list = []
        self.schedule_lines: list = []
        self.ledger_text: str | None = None

    def event(self, t: int, agent: str, parent, event: str, payload: dict, ledger_ref=None) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown event type {event}")
        self.event_lines.append(dumps({
            "episode_id": self.episode_id, "seed": self.seed, "t": t, "agent": agent,
            "parent": parent, "event": event, "payload": payload, "ledger_ref": ledger_ref,
        }))

    def schedule(self, record: dict) -> None:
        self.schedule_lines.append(dumps(record))

    def ledger(self, ledger: dict) -> None:
        self._ledger = ledger  # serialized lazily (the env hands over a private deep copy)
        self.ledger_text = None

    def _ledger_text(self) -> str:
        if self.ledger_text is None and getattr(self, "_ledger", None) is not None:
            self.ledger_text = json.dumps(self._ledger, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
        return self.ledger_text or ""

    # parsed views ----------------------------------------------------------
    def events(self) -> list:
        return [json.loads(x) for x in self.event_lines]

    def schedule_records(self) -> list:
        return [json.loads(x) for x in self.schedule_lines]

    def ledger_dict(self) -> dict:
        text = self._ledger_text()
        return json.loads(text) if text else {}

    def texts(self) -> dict:
        return {
            "episode.jsonl": "".join(x + "\n" for x in self.event_lines),
            "scheduler.jsonl": "".join(x + "\n" for x in self.schedule_lines),
            "ledger.json": self._ledger_text(),
        }

    def write(self, out_dir, manifest: dict) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        texts = self.texts()
        for name, text in texts.items():
            (out / name).write_text(text, encoding="utf-8")
        manifest = dict(manifest)
        manifest["files_sha256"] = {n: sha256_text(t) for n, t in texts.items()}
        (out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        return out


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_episode(ep_dir) -> dict:
    d = Path(ep_dir)
    return {
        "events": read_jsonl(d / "episode.jsonl"),
        "schedule": read_jsonl(d / "scheduler.jsonl"),
        "ledger": json.loads((d / "ledger.json").read_text(encoding="utf-8")),
        "manifest": json.loads((d / "manifest.json").read_text(encoding="utf-8")) if (d / "manifest.json").exists() else {},
    }


def git_commit(repo_dir=None) -> dict:
    repo_dir = repo_dir or Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo_dir), "status", "--porcelain", "--", "dst"], capture_output=True, text=True, timeout=10).stdout.strip() != ""
        return {"git_commit": sha or None, "git_dirty_dst": dirty}
    except Exception:  # git missing: still produce a manifest
        return {"git_commit": None, "git_dirty_dst": None}


def base_manifest() -> dict:
    m = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
         "python": sys.version.split()[0], "platform": sys.platform}
    m.update(git_commit())
    if os.environ.get("DST_RUN_NOTE"):
        m["note"] = os.environ["DST_RUN_NOTE"]
    return m
