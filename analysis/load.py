"""Find episode directories, check their integrity and turn each into one flat row.

An episode directory holds ``ledger.json`` (see docs/LOG_SCHEMA.md). Every directory found
is reported: an episode that cannot be scored is kept in the table with ``included = 0`` and
an ``excluded_reason`` instead of being dropped.

Exclusion reasons (in the order they are checked):

* ``unreadable: ...``      a log file is missing or is not valid JSON
* ``hash_mismatch: ...``   a log file differs from the SHA-256 recorded in its manifest
* ``replay_copy``          a directory written by ``rerun --replay`` (its manifest has
  ``replay_of``); it duplicates its original
* ``aborted``              the manifest marks the run aborted (``aborted: true`` or
  ``status`` in {aborted, error, killed}), or the episode's end reason is ``aborted_*``
  (free-quota refusal or repeated client errors: an invalid sample, rerun by the grid)
* ``client_errors``        only with ``exclude_client_errors``: an LLM call raised

Episodes that ended at ``max_ticks`` or at the team's LLM ``call_cap`` are kept and flagged
(``hit_max_ticks``, ``hit_call_cap``): running out of time or budget is an outcome, not a
broken run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dst.logging import LOG_FILES, load_episode
from dst.metrics import episode_metrics

ABORT_STATUSES = ("aborted", "error", "killed")


def find_episode_dirs(roots) -> list:
    """Every directory under ``roots`` (recursively, symlinks not followed) that holds a
    ``ledger.json`` or a ``manifest.json``, sorted by path."""
    out = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            raise FileNotFoundError(f"no such runs directory: {root}")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            if "ledger.json" in filenames or "manifest.json" in filenames:
                out.add(Path(dirpath))
    return sorted(out, key=lambda p: str(p))


def fault_profile(faults: dict | None) -> str:
    if not faults:
        return "unknown"
    vals = [faults.get(k) for k in ("p_timeout", "p_bounce", "p_stale_board", "delivery_delay")]
    if all(v in (0, 0.0) for v in vals[:3]):
        return "fault-free"
    return "t{}-b{}-s{}-d{}".format(*vals)


def model_label(manifest: dict, ledger: dict | None = None) -> str:
    agents = manifest.get("agents") or "unknown"
    if agents == "llm":
        return str(manifest.get("model") or "llm-unknown")
    return str(agents)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get(d, *keys, default=0):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def load_rows(roots, base=None, exclude_client_errors=False, check_hashes=True) -> tuple:
    """Returns (rows, metrics, ledgers); the dicts are keyed by ``run_dir``. ``rows`` covers
    every directory found, scored or not."""
    rows, metrics, ledgers = [], {}, {}
    base = Path(base) if base else None
    for d in find_episode_dirs(roots):
        rel = os.path.relpath(d, base) if base else str(d)
        row = {"run_dir": rel, "included": 0, "excluded_reason": ""}
        manifest = {}
        try:
            mp = d / "manifest.json"
            manifest = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        except Exception as e:  # noqa: BLE001 - reported, not raised
            row["excluded_reason"] = f"unreadable: manifest.json ({type(e).__name__})"
        row.update({
            "episode_id": manifest.get("episode_id", d.name), "model": model_label(manifest),
            "agents": manifest.get("agents", ""), "provider": manifest.get("provider") or "",
            "condition": manifest.get("condition", ""), "shape": manifest.get("shape", ""),
            "seed": manifest.get("seed", ""), "faults": fault_profile(manifest.get("faults")),
            "n_messages": _get(manifest, "batch", "n_messages", default=""),
            "n_repeat": _get(manifest, "batch", "n_repeat", default=""),
            "max_retries": manifest.get("max_retries", ""), "git_commit": (manifest.get("git_commit") or "")[:12],
            "aborted": int(bool(manifest.get("aborted")) or str(manifest.get("status", "")).lower() in ABORT_STATUSES),
        })
        ep = None
        if not row["excluded_reason"]:
            missing = [n for n in LOG_FILES if not (d / n).exists()]
            if missing:
                row["excluded_reason"] = "unreadable: missing " + ",".join(missing)
            else:
                try:
                    ep = load_episode(d)
                except Exception as e:  # noqa: BLE001
                    row["excluded_reason"] = f"unreadable: {type(e).__name__}"
        if ep is not None and check_hashes and manifest.get("files_sha256"):
            bad = [n for n, h in sorted(manifest["files_sha256"].items()) if (d / n).exists() and _sha(d / n) != h]
            if bad:
                row["excluded_reason"] = "hash_mismatch: " + ",".join(bad)
        if ep is not None and not row["excluded_reason"] and manifest.get("replay_of"):
            row["excluded_reason"] = "replay_copy"
        if ep is not None and not row["excluded_reason"] and row["aborted"]:
            row["excluded_reason"] = "aborted"
        m = None
        if ep is not None:
            led = ep["ledger"]
            # fall back to the ledger for settings the manifest lacks
            for k in ("condition", "shape", "seed", "episode_id"):
                if row[k] in ("", None):
                    row[k] = led.get(k, "")
            try:
                m = episode_metrics(led, ep["schedule"], ep["events"])
            except Exception as e:  # noqa: BLE001
                if not row["excluded_reason"]:
                    row["excluded_reason"] = f"unscorable: {type(e).__name__}: {e}"[:200]
        if m is not None:
            row.update(metric_fields(m))
            # an episode stopped by a quota refusal or repeated client errors is an invalid sample
            if str(m["end_reason"]).startswith("aborted") and not row["aborted"]:
                row["aborted"] = 1
                if not row["excluded_reason"]:
                    row["excluded_reason"] = "aborted"
            if exclude_client_errors and m["cost"]["client_errors"] and not row["excluded_reason"]:
                row["excluded_reason"] = "client_errors"
            metrics[rel] = m
            ledgers[rel] = ep["ledger"]
        row["included"] = int(not row["excluded_reason"] and m is not None)
        rows.append(row)
    return rows, metrics, ledgers


def metric_fields(m: dict) -> dict:
    """Flatten one ``dst.metrics.episode_metrics`` result into CSV columns."""
    f = {
        "end_reason": m["end_reason"], "hit_max_ticks": int(m["end_reason"] == "max_ticks"),
        "hit_call_cap": int(m["end_reason"] == "call_cap"),
        "dar_extras": m["dar"]["extras"], "dar_required": m["dar"]["required"], "dar_per100": m["dar"]["per100"],
        "any_duplicate": int(m["dar"]["any_duplicate"]),
        "dup_same_agent": m["dar"]["by_cause"]["same_agent"], "dup_cross_agent": m["dar"]["by_cause"]["cross_agent"],
    }
    for role in ("all", "worker", "manager", "lead"):
        k, n = m["fcr"].get(role, [0, 0])
        f[f"fcr_{role}_k"], f[f"fcr_{role}_n"] = k, n
    for role in ("all", "manager", "lead"):
        k, n = m["pcr"].get(role, [0, 0])
        f[f"pcr_{role}_k"], f[f"pcr_{role}_n"] = k, n
    f["any_false_done"] = int(f["fcr_all_k"] > 0)
    f["any_premature"] = int(f["pcr_all_k"] > 0)
    f["any_failure"] = int(f["any_duplicate"] or f["any_false_done"])
    f["lead_done"] = int(m["lead_done"])
    f["task_met"], f["task_total"] = m["task_success"]["met"], m["task_success"]["total"]
    f["task_complete"] = int(f["task_met"] == f["task_total"])
    for k in ("claimed", "claimed_true", "met", "met_claimed"):
        f[f"fid_{k}"] = m["fidelity"][k]
    c = m["cost"]
    for k in ("steps", "ticks", "effects", "suppressed", "blocked", "refused_reports", "llm_calls", "input_tokens",
              "output_tokens", "estimated_token_calls", "parse_failures", "client_errors"):
        f[k] = c.get(k, 0)
    f["tokens"] = f["input_tokens"] + f["output_tokens"]
    return f


def original_episode_dirs(roots) -> list:
    """Episode directories with a ledger that are not replay copies (for sampling)."""
    out = []
    for d in find_episode_dirs(roots):
        mp = d / "manifest.json"
        try:
            m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        except Exception:  # noqa: BLE001
            continue
        if m.get("replay_of") or not (d / "ledger.json").exists():
            continue
        out.append(d)
    return out
