"""Scan a git repository for credentials and forbidden words, without printing them.

Scanned: every tracked file in the working tree (``git ls-files``), every line added in
any commit of any ref (``git log -p --all``), and every commit message. A hit is reported
as ``where:line  pattern-name``; the matched text itself is never printed or returned.

Forbidden words come from a local file (one word or phrase per line, ``#`` comments,
case-insensitive, matched on word boundaries; a line ``re:<regex>`` is a raw regex). The
file is passed on the command line and is never committed, so the list stays private.
"""
from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from pathlib import Path

# The patterns are assembled from pieces so this file does not match itself.
_SECRETS_DIR = r"\." + "sec" + r"rets[/\\]"

PATTERNS = [
    ("google-api-key", re.compile("AI" + r"za[0-9A-Za-z_\-]{35}")),
    ("github-classic-token", re.compile(r"\b" + "gh" + r"[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github-fine-grained-token", re.compile("github" + r"_pat_[A-Za-z0-9_]{22,}")),
    ("cloudflare-api-token", re.compile(r"(?i)(?:cloudflare|\bcf)[_\-\s]*(?:api)?[_\-\s]*(?:token|key)\b[\"']?\s*[:=]\s*"
                                        r"[\"']?[A-Za-z0-9_\-]{37,45}\b")),
    ("cloudflare-global-key", re.compile(r"(?i)x-auth-key[\"']?\s*[:=]\s*[\"']?[0-9a-f]{37}\b")),
    ("openai-style-key", re.compile(r"\b" + "s" + r"k-(?:[A-Za-z0-9]+-)?[A-Za-z0-9_\-]{20,}")),
    ("aws-access-key-id", re.compile(r"\b(?:AK" + r"IA|AS" + r"IA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxo" + r"x[abprs]-[A-Za-z0-9\-]{10,}")),
    ("private-key-block", re.compile("-----BEGIN " + r"(?:[A-Z]+ )?PRIVATE KEY-----")),
    ("secrets-path", re.compile(_SECRETS_DIR)),
]
# generic: a bearer token or an assignment to a secret-looking name, kept only if the value
# has high entropy and mixes letters and digits (filters placeholders like "<your-key>")
BEARER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9_\-\.=+/]{20,})")
ASSIGN = re.compile(r"(?i)(?:api[_\-]?key|secret|token|passw(?:or)?d|auth)[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']"
                    r"([A-Za-z0-9_\-\.=+/]{20,})[\"']")


def entropy(s: str) -> float:
    c = Counter(s)
    return -sum(v / len(s) * math.log2(v / len(s)) for v in c.values())


def _high_entropy(v: str) -> bool:
    return (len(v) >= 20 and entropy(v) >= 3.5 and any(ch.isdigit() for ch in v)
            and any(ch.isalpha() for ch in v))


def load_words(path) -> list:
    """(name, regex) per forbidden word; the name is ``word#<n>`` so output never shows it."""
    out = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        w = line.strip()
        if not w or w.startswith("#"):
            continue
        rx = w[3:] if w.startswith("re:") else r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])"
        out.append((f"forbidden-word#{i}", re.compile(rx, re.IGNORECASE)))
    return out


def scan_text(text: str, words=()) -> list:
    """Pattern names that hit in ``text`` (names only, never the match)."""
    hits = [name for name, rx in PATTERNS if rx.search(text)]
    if any(_high_entropy(m.group(1)) for m in BEARER.finditer(text)):
        hits.append("generic-bearer-token")
    if any(_high_entropy(m.group(1)) for m in ASSIGN.finditer(text)):
        hits.append("generic-secret-assignment")
    hits += [name for name, rx in words if rx.search(text)]
    return hits


def _git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          errors="replace").stdout


def scan_repo(repo, words=(), history=True) -> list:
    """List of (where, line_no, pattern_name). ``where`` is a path for the working tree,
    ``<sha12>:<path>`` for history, ``<sha12>:COMMIT_MSG`` for commit messages."""
    repo = Path(repo)
    hits = []
    for rel in _git(repo, "ls-files", "-z").split("\0"):
        if not rel:
            continue
        p = repo / rel
        for name in scan_text(rel, words):  # file names too
            hits.append((rel, 0, name + " (in path)"))
        if not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for name in scan_text(line, words):
                hits.append((rel, i, name))
    if history:
        sha, path, new_line = "", "", 0
        for line in _git(repo, "log", "-p", "--all", "--no-color", "--no-ext-diff", "-U0", "--format=commit %H").splitlines():
            if line.startswith("commit ") and len(line) == 47:
                sha, path = line[7:19], ""
            elif line.startswith("+++ "):
                path = line[6:] if line.startswith("+++ b/") else line[4:]
            elif line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                new_line = int(m.group(1)) if m else 0
            elif line.startswith("+") and not line.startswith("+++"):
                for name in scan_text(line[1:], words):
                    hits.append((f"{sha}:{path}", new_line, name))
                new_line += 1
        for block in _git(repo, "log", "--all", "--format=%H%x00%B%x01").split("\x01"):
            if "\x00" not in block:
                continue
            h, body = block.strip("\n").split("\x00", 1)
            for i, line in enumerate(body.splitlines(), 1):
                for name in scan_text(line, words):
                    hits.append((f"{h[:12]}:COMMIT_MSG", i, name))
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out
