#!/usr/bin/env python3
"""Scan this git repository (tracked files, full history, commit messages) for credentials
and, optionally, for a private list of forbidden words. Never prints a matched value.

    python scripts/secret_scan.py [--repo .] [--words /path/outside/repo/words.txt] [--no-history]

Patterns: Google API keys, GitHub classic and fine-grained tokens, Cloudflare API tokens
and global keys, OpenAI-style keys, AWS key ids, Slack tokens, private-key blocks, paths
into a secrets directory, and generic high-entropy bearer tokens or secret assignments.
The words file holds one word or phrase per line (``#`` comments, ``re:<regex>`` for a
regex); keep it outside the repository or untracked. Output: ``location:line  pattern``
(words are shown as ``forbidden-word#<line in the words file>``). Exit 1 on any hit.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.secretscan import load_words, scan_repo  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--words", help="local, untracked file of forbidden words")
    ap.add_argument("--no-history", action="store_true", help="scan only the tracked files")
    a = ap.parse_args(argv)
    words = []
    if a.words:
        wp = Path(a.words).resolve()
        words = load_words(wp)
        try:
            rel = wp.relative_to(Path(a.repo).resolve())
            tracked = subprocess.run(["git", "-C", a.repo, "ls-files", "--error-unmatch", str(rel)],
                                     capture_output=True).returncode == 0
            if tracked:
                print("refusing: the words file is tracked by git; keep it outside the repository or untracked")
                return 2
        except ValueError:
            pass  # outside the repo: fine
    hits = scan_repo(a.repo, words, history=not a.no_history)
    files = subprocess.run(["git", "-C", a.repo, "ls-files"], capture_output=True, text=True).stdout.count("\n")
    commits = subprocess.run(["git", "-C", a.repo, "rev-list", "--all", "--count"], capture_output=True,
                             text=True).stdout.strip()
    scope = f"{files} tracked files" + ("" if a.no_history else f", {commits} commits (diffs and messages)")
    print(f"secret scan: {scope}; {len(words)} forbidden words")
    for where, line, name in hits:
        print(f"  HIT {where}:{line}  {name}")
    print(f"result: {'CLEAN' if not hits else f'{len(hits)} hit(s)'}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
