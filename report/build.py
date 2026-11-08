#!/usr/bin/env python3
"""Build the sprint report PDF on the official Apart template.

    .venv/bin/python report/build.py --grid grid            # the submission (results/grid, figures/grid)
    .venv/bin/python report/build.py --grid scripted-demo --demo   # pipeline test, NOT the submission
    .venv/bin/python report/build.py --grid grid --final    # also fail on TODO, length and abstract limits

What it does:

1. Reads ``report/report.md`` (a small markdown dialect, below), resolves every number placeholder
   from the analysis tables written by ``scripts/analyze.py`` (``results/<grid>/table1.csv``,
   ``tests.csv``, ``validation.csv``, ``episodes.csv``, ``summary.json``) and logs each one to
   ``<out>/numbers.csv`` (placeholder, file, sha256, matched rows, raw value, printed text).
   An unresolved or ambiguous placeholder is an error: the build stops.
2. Fills ``report/template/template.docx`` (the official template, exported unchanged from the
   Google Doc): title (the template's footnote kept), author block, abstract box; removes the
   info box and every guidance paragraph; writes the sections by cloning the template's own
   heading, body and list paragraphs, so fonts, sizes and spacing are the template's.
3. Converts the docx to PDF with LibreOffice (``nice -n 10``, one conversion at a time through a
   lock file, the office process closed after) and prints the page count before the References
   heading and in total, the abstract word count and the file size; it also checks the PDF text
   for leftover template guidance and for a list of words the report must not use.

Source dialect (``report.md``):

* front matter between ``---`` lines: ``title:``, ``author:``, ``affiliation:``
* ``# Heading`` = a template section heading (Heading 2), ``## Heading`` = a sub-heading (Heading 3);
  the section ``# Abstract`` fills the abstract box.
* paragraphs separated by blank lines; ``**bold**``, ``*italic*``, ```code```, ``[text](url)``;
  citations ``[@key]`` / ``[@a; @b]`` (numbered by first use; keys from ``references.json``,
  verified entries only; the reference list is written at ``!references``).
* ``- item`` bullets and ``1. item`` numbered lists (indented continuation lines join the item).
* ``Table: caption {#tab:id}`` directly above a pipe table; ``![caption](path){#fig:id width=6.5}``
  for a figure (PNG, or PDF rasterized at 300 dpi); ``@tab:id`` / ``@fig:id`` print "Table 1" /
  "Figure 2". ``!csv[caption](file){#tab:id cols=col:Header:fmt,... where=k=v&k=v}`` makes a
  table straight from a results CSV (every cell logged in numbers.csv).
* fenced code blocks (monospace); ``!prompts`` inserts the agents' prompts generated from
  ``dst/agents/prompts.py``; ``!include(path)``; ``\\newpage``; ``<!-- comments -->`` dropped.
* ``::: demo`` ... ``:::`` only in ``--demo`` builds, ``::: nodemo`` ... ``:::`` only in others.
* A paragraph starting ``TODO`` is printed highlighted; ``--final`` refuses to build with one.

Placeholders (only numbers that come from the analysis tables):

    {{T1:<model>:<cond>:<metric>[|fmt]}}               Table 1 row, shape "all" (shapes pooled)
    {{T1:<model>:<shape>:<cond>:<metric>[|fmt]}}       Table 1 row for one shape
    {{TT:<model>[:<shape>]:<control>:<outcome>:<col>[|fmt]}}   tests.csv (B0 vs control)
    {{V:<agents>:<shape>:<cond>:<col>[|fmt]}}          validation.csv (scripted vs analytic)
    {{EP:<model>:<shape>:<cond>:<col>|<agg>}}          episodes.csv, included episodes only
    {{S:<dotted.key>}}                                 summary.json
    {{M:<view>:<monitor>:<group>:<metric>[:<col>][|fmt]}}  log-only monitor summary (Track 4)
    {{T1@<other-grid>:...}}                            read results/<other-grid>/ instead

``<model>`` may be an alias from ``report/config.json`` (``A``, ``B``); any field may be ``*`` or a
comma list. Metrics: any CSV column, plus for a rate column R (one with R_k, R_n, R_lo, R_hi):
``R`` (percent), ``R_ci`` ("43% [30, 57]"), ``R_kn`` ("6/14"), ``R_full``; ``dar`` (DAR per 100,
one decimal), ``dar_ci``, ``dar_full``. fmt: a Python format spec (``.2f``), ``pct``, ``pct1``,
``int``, ``p`` (p-value), ``signpct``, ``ptimeout``; an aggregate first when several rows match:
``sum``, ``mean``, ``min``, ``max``, ``maxabs``, ``count``, e.g. ``|sum;,.0f``.

Monitor placeholders (``M``) read the long-format ``summary.csv`` written by ``scripts/run_monitor.py``
(columns monitor, view, group, metric, k, n, value, ci95_lo, ci95_hi). ``<view>`` is a key of
``monitor_views`` in ``report/config.json``, which names the file (relative to ``results/``) and the
number of episodes the finished run must cover; rows are also filtered on the CSV's ``view`` column, so a
merged file (``monitor/grid-summary.csv``) works as well. ``<monitor>``: ``llm`` or ``baseline``;
``<group>``: ``all``, ``team_model=<model or alias>``, ``condition=B0``, ``shape=D``; ``<col>``:
``value`` (default), ``k``, ``n``, ``ci`` ("43% [30, 57]"), ``kn`` ("6/14"), ``full``. A rate (Wilson
row) prints as a percent by default, an F1 (bootstrap row) with two decimals. While the run is not
finished (fewer episodes than configured, or no file yet) the placeholder prints ``[monitor pending]``,
the build warns, and ``--final`` refuses to build.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

import docx  # noqa: E402
from docx.opc.constants import RELATIONSHIP_TYPE as RT  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Inches  # noqa: E402
from lxml import etree  # noqa: E402

TEMPLATE = HERE / "template" / "template.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FONT = "Old Standard TT"
MONO = "Courier New"
LINK_COLOR = "1155cc"      # the template's own link colour (footnote link)
LOCK = Path("/tmp/dst-report-soffice.lock")

# Template guidance that must not survive into the PDF (checked on the PDF text).
GUIDANCE = [
    "PROJECT TITLE", "Author name", "Summarize your project", "How to use this template",
    "How your submission will be evaluated", "Recommended length", "What problem are you addressing",
    "Aspire to clearly list", "What prior work is most similar", "Describe your approach clearly",
    "Present your main findings", "Tips for figures", "Discuss the broader implications",
    "What are the limitations of your work", "What are the natural next steps",
    "Briefly summarize your main findings", "Include links if applicable", "[Link to GitHub",
    "A.B. led the project", "Use a consistent citation format", "[Reference 1]",
    "Supplementary material such as", "If you used LLM assistance", "NOTE: We strongly encourage",
    "to brainstorm approaches and help draft",
]
# Extra words the PDF must not contain: one regular expression per line, read from the file named by
# --banned-file or $DST_REPORT_BANNED (kept outside the repository). Empty when neither is set.
BANNED: list = []


class BuildError(Exception):
    pass


# =========================================================================================
# placeholders
# =========================================================================================

PH_RE = re.compile(r"\{\{([A-Z0-9]+)(?:@([\w.\-]+))?:([^{}|]*?)(?:\|([^{}]*))?\}\}")
AGGS = ("sum", "mean", "min", "max", "maxabs", "count")


def _num(v):
    if v is None or v == "":
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def fmt_value(v, spec: str | None, default: str | None = None) -> str:
    spec = spec if spec else default
    if isinstance(v, str) and spec not in ("ptimeout",):
        return v
    if spec == "ptimeout":
        m = re.match(r"t([\d.]+)-", str(v))
        if not m:
            raise BuildError(f"cannot read p_timeout from fault profile {v!r}")
        return m.group(1)
    if v is None or _isnan(v):
        return "n/a"
    if spec in (None, ""):
        return f"{int(v):d}" if float(v).is_integer() else f"{v:.1f}"
    if spec == "int":
        return f"{int(round(v)):d}"
    if spec == "pct":
        return f"{100 * v:.0f}%"
    if spec == "pct1":
        return f"{100 * v:.1f}%"
    if spec == "pctnum":
        return f"{100 * v:.0f}"
    if spec == "signpct":
        return f"{100 * v:+.0f}%"
    if spec == "p":
        if v < 0.001:
            return "< 0.001"
        return f"{v:.3f}" if v < 0.1 else f"{v:.2f}"
    try:
        return format(v, spec)
    except (ValueError, TypeError) as e:
        raise BuildError(f"bad format {spec!r} for {v!r}: {e}")


class Tables:
    """Loads results/<grid>/ CSVs lazily and resolves placeholders against them."""

    def __init__(self, grid: str, config: dict):
        self.grid = grid
        self.config = config
        self._cache = {}
        self.log = []          # rows for numbers.csv
        self.pending = []      # monitor placeholders whose run is not finished

    def path(self, grid: str, name: str) -> Path:
        return REPO / "results" / grid / name

    def load(self, grid: str, name: str):
        key = (grid, name)
        if key not in self._cache:
            p = self.path(grid, name)
            if not p.exists():
                raise BuildError(f"missing analysis output {p.relative_to(REPO)} (run scripts/analyze.py)")
            data = p.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if name.endswith(".json"):
                obj = json.loads(data)
            else:
                obj = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
            self._cache[key] = (obj, sha)
        return self._cache[key]

    def models(self, grid: str) -> dict:
        g = self.config.get("grids", {}).get(grid, {})
        return g.get("models", {})

    def _match(self, row_val: str, sel: str, aliases: dict | None = None) -> bool:
        if sel == "*":
            return True
        opts = [s.strip() for s in sel.split(",")]
        if aliases:
            opts = [aliases.get(o, o) for o in opts]
        return row_val in opts

    def _faults(self, grid: str, rows: list) -> str | None:
        f = self.config.get("grids", {}).get(grid, {}).get("faults")
        if f:
            return f
        profiles = sorted({r.get("faults") for r in rows if r.get("faults")})
        if len(profiles) > 1:
            raise BuildError(f"results/{grid} has several fault profiles {profiles}: set grids.{grid}.faults in "
                             "report/config.json")
        return None

    def resolve(self, m: re.Match) -> str:
        kind, grid, body, fmtspec = m.group(1), m.group(2) or self.grid, m.group(3), m.group(4)
        parts = [p.strip() for p in body.split(":")]
        agg, spec = None, None
        if fmtspec:
            fs = fmtspec.split(";")
            if fs[0] in AGGS:
                agg, fs = fs[0], fs[1:]
            spec = ";".join(fs) if fs else None
        aliases = self.models(grid)
        if kind == "S":
            obj, sha = self.load(grid, "summary.json")
            v = obj
            for k in body.split("."):
                if not isinstance(v, dict) or k not in v:
                    raise BuildError(f"summary.json has no key {body!r}")
                v = v[k]
            text = fmt_value(_num(v) if not isinstance(v, (list, dict)) else json.dumps(v), spec)
            self.log.append({"placeholder": m.group(0), "file": f"results/{grid}/summary.json", "sha256": sha,
                             "rows": body, "raw": v, "text": text})
            return text
        if kind == "M":
            return self.resolve_monitor(m, parts, spec)
        if kind == "T1":
            name = "table1.csv"
            if len(parts) == 3:
                keys = {"model": parts[0], "shape": "all", "condition": parts[1]}
                col = parts[2]
            elif len(parts) == 4:
                keys = {"model": parts[0], "shape": parts[1], "condition": parts[2]}
                col = parts[3]
            else:
                raise BuildError(f"{m.group(0)}: T1 needs model:cond:metric or model:shape:cond:metric")
        elif kind == "TT":
            name = "tests.csv"
            if len(parts) == 4:
                keys = {"model": parts[0], "shape": "all", "control": parts[1], "outcome": parts[2]}
                col = parts[3]
            elif len(parts) == 5:
                keys = {"model": parts[0], "shape": parts[1], "control": parts[2], "outcome": parts[3]}
                col = parts[4]
            else:
                raise BuildError(f"{m.group(0)}: TT needs model[:shape]:control:outcome:column")
        elif kind == "V":
            name = "validation.csv"
            if len(parts) != 4:
                raise BuildError(f"{m.group(0)}: V needs agents:shape:cond:column")
            keys = {"agents": parts[0], "shape": parts[1], "condition": parts[2]}
            col = parts[3]
        elif kind == "EP":
            name = "episodes.csv"
            if len(parts) != 4:
                raise BuildError(f"{m.group(0)}: EP needs model:shape:cond:column")
            keys = {"model": parts[0], "shape": parts[1], "condition": parts[2], "included": "1"}
            col = parts[3]
            if not agg:
                raise BuildError(f"{m.group(0)}: EP needs an aggregate (|sum, |mean, |count, ...)")
        else:
            raise BuildError(f"{m.group(0)}: unknown placeholder kind {kind!r}")
        rows, sha = self.load(grid, name)
        f = self._faults(grid, rows) if "faults" in (rows[0] if rows else {}) else None
        hit = [r for r in rows
               if all(self._match(r.get(k, ""), v, aliases if k in ("model", "agents") else None)
                      for k, v in keys.items())
               and (f is None or r.get("faults") == f)]
        if not hit:
            raise BuildError(f"{m.group(0)}: no row in results/{grid}/{name} matches {keys}"
                             + (f" faults={f}" if f else ""))
        if len(hit) > 1 and not agg:
            raise BuildError(f"{m.group(0)}: {len(hit)} rows match {keys}; add an aggregate (|sum, |mean, ...)")
        text, raw = self._metric(hit, col, spec, agg, m.group(0), name)
        desc = "; ".join(",".join(f"{k}={r.get(k)}" for k in keys) for r in hit[:12]) + (" ..." if len(hit) > 12 else "")
        self.log.append({"placeholder": m.group(0), "file": f"results/{grid}/{name}", "sha256": sha,
                         "rows": desc, "raw": raw, "text": text})
        return text

    def resolve_monitor(self, m: re.Match, parts: list, spec: str | None) -> str:
        ph = m.group(0)
        if len(parts) not in (4, 5):
            raise BuildError(f"{ph}: M needs view:monitor:group:metric[:col]")
        view, mon, group, metric = parts[:4]
        col = parts[4] if len(parts) == 5 else "value"
        views = self.config.get("monitor_views", {})
        if view not in views:
            raise BuildError(f"{ph}: no monitor view {view!r} in report/config.json monitor_views")
        vcfg = views[view]
        rel_file = vcfg["file"]
        expect = int(vcfg.get("episodes", 0))
        csv_view = vcfg.get("view", view)
        if group.startswith("team_model="):
            alias = self.models(self.grid)
            group = "team_model=" + alias.get(group.split("=", 1)[1], group.split("=", 1)[1])
        p = REPO / "results" / rel_file
        if not p.exists():
            return self._pending(ph, rel_file, "no file yet")
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        rows = [r for r in csv.DictReader(io.StringIO(data.decode("utf-8"))) if r.get("view") == csv_view]
        done = [r for r in rows if r["monitor"] == mon and r["group"] == "all" and r["metric"] == "episodes"]
        n_eps = int(float(done[0]["k"])) if done and done[0]["k"] not in ("", None) else 0
        if expect and n_eps < expect:
            return self._pending(ph, rel_file, f"{n_eps}/{expect} episodes")
        hit = [r for r in rows if r["monitor"] == mon and r["group"] == group and r["metric"] == metric]
        if len(hit) != 1:
            raise BuildError(f"{ph}: {len(hit)} rows in results/{rel_file} for view={csv_view} monitor={mon} "
                             f"group={group} metric={metric} (need exactly 1)")
        r = hit[0]
        wilson = r.get("ci_kind") == "wilson"
        boot = r.get("ci_kind") == "episode_bootstrap"
        v, lo, hi = _num(r.get("value")), _num(r.get("ci95_lo")), _num(r.get("ci95_hi"))
        p_ = spec or ("pct" if wilson else ".2f")
        num = (lambda x: fmt_value(x, "pctnum")) if p_ == "pct" else (lambda x: fmt_value(x, p_))
        if col == "value":
            text = fmt_value(_num(r["k"]), spec) if not (wilson or boot) else fmt_value(v, p_)
        elif col in ("k", "n"):
            text = fmt_value(_num(r[col]), spec)
        elif col in ("ci", "full"):
            if not (wilson or boot):
                raise BuildError(f"{ph}: metric {metric} has no interval")
            text = f"{fmt_value(v, p_)} [{num(lo)}, {num(hi)}]"
            if col == "full" and wilson:
                text += f" ({r['k']}/{r['n']})"
        elif col == "kn":
            if not wilson:
                raise BuildError(f"{ph}: kn needs a rate metric")
            text = f"{r['k']}/{r['n']}"
        else:
            raise BuildError(f"{ph}: unknown monitor column {col!r} (value, k, n, ci, kn, full)")
        self.log.append({"placeholder": ph, "file": f"results/{rel_file}", "sha256": sha,
                         "rows": f"view={csv_view},monitor={mon},group={group},metric={metric}",
                         "raw": [r.get("k"), r.get("n"), r.get("value"), r.get("ci95_lo"), r.get("ci95_hi")],
                         "text": text})
        return text

    def _pending(self, ph, rel_file, why):
        self.pending.append((ph, rel_file, why))
        self.log.append({"placeholder": ph, "file": f"results/{rel_file}", "sha256": "", "rows": why,
                         "raw": "pending", "text": "[monitor pending]"})
        return "[monitor pending]"

    def _metric(self, rows, col, spec, agg, ph, name):
        cols = rows[0].keys()

        def get(r, c):
            if c not in r:
                raise BuildError(f"{ph}: no column {c!r} in {name}")
            return _num(r[c])

        def is_rate(base):
            return all(f"{base}{s}" in cols for s in ("_k", "_n", "_lo", "_hi")) and base in cols

        if len(rows) == 1 and not agg:
            r = rows[0]
            if col in ("dar", "dar_ci", "dar_full"):
                v, lo, hi = get(r, "dar_per100"), get(r, "dar_per100_lo"), get(r, "dar_per100_hi")
                s = spec or ".1f"
                ci = f"[{fmt_value(lo, s)}, {fmt_value(hi, s)}]"
                out = {"dar": fmt_value(v, s), "dar_ci": ci, "dar_full": f"{fmt_value(v, s)} {ci}"}[col]
                return out, [v, lo, hi]
            for suf in ("_ci", "_kn", "_full"):
                if col.endswith(suf) and is_rate(col[: -len(suf)]):
                    base = col[: -len(suf)]
                    v, lo, hi = get(r, base), get(r, base + "_lo"), get(r, base + "_hi")
                    k, n = r[base + "_k"], r[base + "_n"]
                    if n in ("", "0"):
                        return "n/a (0 reports)", [k, n]
                    p = spec or "pct"
                    num = (lambda x: fmt_value(x, "pctnum")) if p == "pct" else (lambda x: fmt_value(x, p))
                    ci = f"{fmt_value(v, p)} [{num(lo)}, {num(hi)}]"
                    out = {"_ci": ci, "_kn": f"{k}/{n}", "_full": f"{ci} ({k}/{n})"}[suf]
                    return out, [v, lo, hi, k, n]
            v = get(r, col)
            default = "pct" if is_rate(col) else None
            return fmt_value(v, spec, default), v
        vals = [get(r, col) for r in rows]
        nums = [v for v in vals if isinstance(v, float) and not math.isnan(v)]
        if agg == "count":
            v = float(len(rows))
        elif not nums:
            v = float("nan")
        elif agg == "sum":
            v = sum(nums)
        elif agg == "mean":
            v = sum(nums) / len(nums)
        elif agg == "min":
            v = min(nums)
        elif agg == "max":
            v = max(nums)
        elif agg == "maxabs":
            v = max(abs(x) for x in nums)
        else:
            raise BuildError(f"{ph}: unknown aggregate {agg}")
        return fmt_value(v, spec), {"agg": agg, "n_rows": len(rows), "value": v}


def resolve_placeholders(text: str, tables: Tables) -> str:
    errors = []

    failed = set()

    def sub(m):
        try:
            return tables.resolve(m)
        except BuildError as e:
            msg = str(e)
            errors.append(msg if msg.startswith(m.group(0)) else f"{m.group(0)}: {msg}")
            failed.add(m.group(0))
            return m.group(0)

    out = PH_RE.sub(sub, text)
    for x in re.findall(r"\{\{[^}]*\}\}", out):
        if x not in failed:
            errors.append(f"malformed placeholder {x}")
    if errors:
        raise BuildError("unresolved placeholders:\n  " + "\n  ".join(errors))
    return out


# =========================================================================================
# markdown dialect -> blocks
# =========================================================================================

def preprocess(text: str, demo: bool, base: Path) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

    def inc(m):
        p = (base / m.group(1).strip()).resolve()
        return preprocess(p.read_text(encoding="utf-8"), demo, p.parent)

    text = re.sub(r"^!include\(([^)]+)\)\s*$", inc, text, flags=re.M)
    out, mode = [], None
    for line in text.split("\n"):
        s = line.strip()
        if s in ("::: demo", "::: nodemo"):
            mode = s.split()[1]
            continue
        if s == ":::" and mode:
            mode = None
            continue
        if mode == "demo" and not demo:
            continue
        if mode == "nodemo" and demo:
            continue
        out.append(line)
    return "\n".join(out)


def parse_front(text: str):
    meta = {}
    if text.startswith("---"):
        end = text.index("\n---", 3)
        for line in text[3:end].strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[end + 4:]
    return meta, text


ATTR_RE = re.compile(r"\{([^{}]*)\}\s*$")


def parse_attrs(s: str) -> dict:
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    out = {}
    for tok in re.findall(r"#[\w:\-]+|[\w\-]+=(?:\"[^\"]*\"|\S+)", s):
        if tok.startswith("#"):
            out["id"] = tok[1:]
        else:
            k, v = tok.split("=", 1)
            out[k] = v.strip('"')
    return out


def parse_blocks(text: str) -> list:
    lines = text.split("\n")
    blocks, i = [], 0
    para = []

    def flush():
        if para:
            blocks.append({"t": "p", "text": " ".join(x.strip() for x in para)})
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        if s.startswith("```"):
            flush()
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            blocks.append({"t": "code", "text": "\n".join(code)})
            i = j + 1
            continue
        if s == "\\newpage":
            flush()
            blocks.append({"t": "pagebreak"})
            i += 1
            continue
        if s in ("!prompts", "!references"):
            flush()
            blocks.append({"t": s[1:]})
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            flush()
            blocks.append({"t": "h", "level": len(m.group(1)), "text": m.group(2).strip()})
            i += 1
            continue
        m = re.match(r"^!\[(.*)\]\(([^)]+)\)(\{.*\})?\s*$", s)
        if m:
            flush()
            blocks.append({"t": "fig", "caption": m.group(1), "path": m.group(2),
                           "attrs": parse_attrs(m.group(3) or "")})
            i += 1
            continue
        m = re.match(r"^!csv\[(.*)\]\(([^)]+)\)(\{.*\})?\s*$", s)
        if m:
            flush()
            blocks.append({"t": "csv", "caption": m.group(1), "path": m.group(2),
                           "attrs": parse_attrs(m.group(3) or "")})
            i += 1
            continue
        m = re.match(r"^Table:\s*(.*)$", s)
        if m and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            flush()
            cap = m.group(1)
            attrs = {}
            am = ATTR_RE.search(cap)
            if am:
                attrs = parse_attrs(am.group(1))
                cap = cap[: am.start()].strip()
            j = i + 1
            rows = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                j += 1
            blocks.append({"t": "table", "caption": cap, "attrs": attrs, "rows": rows})
            i = j
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m and not para:
            flush()
            kind = "ol" if m.group(2)[0].isdigit() else "ul"
            items = []
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if mm and (("ol" if mm.group(2)[0].isdigit() else "ul") == kind) and len(mm.group(1)) < 2:
                    items.append(mm.group(3).strip())
                    i += 1
                elif lines[i].startswith("  ") and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            blocks.append({"t": kind, "items": items})
            continue
        para.append(line)
        i += 1
    flush()
    return blocks


# =========================================================================================
# docx writing
# =========================================================================================

def W(tag):
    return qn(tag)


def el(tag, **attrs):
    e = etree.Element(W(tag))
    for k, v in attrs.items():
        e.set(W(f"w:{k}"), str(v))
    return e


PPR_ORDER = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
             "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
             "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid",
             "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
             "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange"]


def ppr_set(ppr, name: str, **attrs):
    """Set one pPr child in schema order (Word rejects out-of-order children), replacing any old one."""
    for old in ppr.findall(W(f"w:{name}")):
        ppr.remove(old)
    new = el(f"w:{name}", **attrs)
    rank = PPR_ORDER.index(name)
    for i, child in enumerate(ppr):
        local = etree.QName(child).localname
        if local in PPR_ORDER and PPR_ORDER.index(local) > rank:
            ppr.insert(i, new)
            return new
    ppr.append(new)
    return new


AFTER = 120   # 6 pt between paragraphs (the template spaces blocks with empty 11 pt lines; this is tighter)


def text_of(p) -> str:
    return "".join(t.text or "" for t in p.iter(W("w:t")))


class Writer:
    def __init__(self, doc, refs: dict, demo: bool, tables: Tables):
        self.doc = doc
        self.body = doc.element.body
        self.refs = refs
        self.demo = demo
        self.tables = tables
        self.cite_order = []
        self.labels = {}
        self.n_fig = 0
        self.n_tab = 0
        self.protos = {}
        self.sect = self.body.find(W("w:sectPr"))
        self.numbering = doc.part.numbering_part.element
        self.todo = 0

    # ---------------------------------------------------------------- template surgery
    def take_prototypes(self):
        by_text = {}
        for p in self.body.iter(W("w:p")):
            by_text.setdefault(text_of(p).strip(), p)
        need = {"h2": "1. Introduction", "h3": "Limitations", "body": "Provide enough background for readers to understand your work.",
                "ol": "[First contribution — what new thing did you create, discover, or demonstrate?]",
                "ul": "Number all figures (Figure 1, Figure 2...) and tables (Table 1, Table 2...)",
                "ref": "[Reference 1]"}
        for k, t in need.items():
            if t not in by_text:
                raise BuildError(f"template paragraph not found: {t!r} (template changed?)")
            self.protos[k] = copy.deepcopy(by_text[t])
        # the spacer the template puts between blocks: an empty paragraph
        for p in self.body.findall(W("w:p")):
            if not text_of(p).strip():
                self.protos["empty"] = copy.deepcopy(p)
                break

    def fill_title_block(self, meta: dict, abstract_blocks: list):
        tbls = self.body.findall(W("w:tbl"))
        title_tbl, info_tbl = tbls[0], tbls[1]
        # title (keep the run that holds the footnote reference)
        for p in title_tbl.iter(W("w:p")):
            if text_of(p).strip() == "PROJECT TITLE":
                runs = p.findall(W("w:r"))
                for r in runs:
                    t = r.find(W("w:t"))
                    if t is not None and t.text == "PROJECT TITLE":
                        t.text = meta["title"]
        # authors: one author cell in the template's nested author grid
        auth_tbl = None
        for t in title_tbl.iter(W("w:tbl")):
            if "Author name 1" in "".join(x.text or "" for x in t.iter(W("w:t"))):
                auth_tbl = t
        if auth_tbl is None:
            raise BuildError("author grid not found in the template")
        rows = auth_tbl.findall(W("w:tr"))
        for r in rows[1:]:
            auth_tbl.remove(r)
        cells = rows[0].findall(W("w:tc"))
        for c in cells[1:]:
            rows[0].remove(c)
        width = 4320  # 3 in: the grid's 690-twip columns collapse in LibreOffice (names wrap per letter)
        grid = auth_tbl.find(W("w:tblGrid"))
        for g in list(grid):
            grid.remove(g)
        grid.append(el("w:gridCol", w=width))
        auth_tbl.find(W("w:tblPr")).find(W("w:tblW")).set(W("w:w"), str(width))
        p = cells[0].find(W("w:p"))
        run = p.find(W("w:r"))
        for x in list(run):
            if x.tag != W("w:rPr"):
                run.remove(x)
        t1 = etree.SubElement(run, W("w:t"))
        t1.text = meta["author"]
        etree.SubElement(run, W("w:br"))
        t2 = etree.SubElement(run, W("w:t"))
        t2.text = meta["affiliation"]
        # abstract box
        abs_p = None
        for p in title_tbl.iter(W("w:p")):
            if text_of(p).startswith("Summarize your project"):
                abs_p = p
        if abs_p is None:
            raise BuildError("abstract paragraph not found in the template")
        ppr = abs_p.find(W("w:pPr"))
        rpr = ppr.find(W("w:rPr"))
        if rpr is not None:
            for u in rpr.findall(W("w:u")):
                rpr.remove(u)
        cell = abs_p.getparent()
        idx = list(cell).index(abs_p)
        cell.remove(abs_p)
        for b in abstract_blocks:
            if b["t"] != "p":
                raise BuildError("the abstract must be plain paragraphs")
            np_ = etree.Element(W("w:p"))
            np_.append(copy.deepcopy(ppr))
            self.add_inline(np_, b["text"])
            cell.insert(idx, np_)
            idx += 1
        # drop the info box and everything after it (guidance), keep sectPr
        started = False
        for child in list(self.body):
            if child is info_tbl:
                started = True
            if started and child.tag != W("w:sectPr"):
                self.body.remove(child)
        # the empty paragraph that sat between title block and info box stays as spacing

    def fix_footnote(self):
        """The Google export numbers its only footnote 0 and has no separator footnotes, so
        LibreOffice prints it without the rule above it. Renumber it 1 and add the standard
        separator footnotes (-1, 0); the footnote text is unchanged."""
        part = None
        for rel in self.doc.part.rels.values():
            if rel.reltype.endswith("/footnotes"):
                part = rel.target_part
        if part is None:
            return
        root = etree.fromstring(part.blob)
        notes = root.findall(W("w:footnote"))
        if any(n.get(W("w:type")) == "separator" for n in notes):
            return
        for n in notes:
            n.set(W("w:id"), str(int(n.get(W("w:id"))) + 1))
        sep = etree.fromstring(
            f'<w:footnote xmlns:w="{W_NS}" w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" w:line="240" '
            f'w:lineRule="auto"/></w:pPr><w:r><w:separator/></w:r></w:p></w:footnote>')
        cont = etree.fromstring(
            f'<w:footnote xmlns:w="{W_NS}" w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" '
            f'w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>')
        root.insert(0, cont)
        root.insert(0, sep)
        part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        for ref in self.body.iter(W("w:footnoteReference")):
            ref.set(W("w:id"), str(int(ref.get(W("w:id"))) + 1))
        # the runs reference a "FootnoteReference" character style the export does not define
        styles = self.doc.styles.element
        if not any(st.get(W("w:styleId")) == "FootnoteReference" for st in styles.findall(W("w:style"))):
            styles.append(etree.fromstring(
                f'<w:style xmlns:w="{W_NS}" w:type="character" w:styleId="FootnoteReference">'
                f'<w:name w:val="footnote reference"/><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'))

    def demo_banner(self):
        sec = self.doc.sections[0]
        p = sec.header.paragraphs[0]
        r = p.add_run("DEMO BUILD (scripted agents, no LLM; pipeline test only). NOT THE SUBMISSION.")
        r.bold = True
        r.font.color.rgb = docx.shared.RGBColor(0xC0, 0x00, 0x00)
        r.font.size = docx.shared.Pt(9)

    # ---------------------------------------------------------------- emit helpers
    def _append(self, e):
        self.sect.addprevious(e)
        return e

    def spacer(self):
        """An empty paragraph (needed after a table); small, so it adds little height."""
        p = etree.Element(W("w:p"))
        ppr = etree.SubElement(p, W("w:pPr"))
        ppr_set(ppr, "spacing", before=0, after=80, line=240, lineRule="auto")
        rpr = etree.SubElement(ppr, W("w:rPr"))
        rpr.append(el("w:sz", val=8))
        self._append(p)

    def _new_from(self, proto_key):
        p = copy.deepcopy(self.protos[proto_key])
        for c in list(p):
            if c.tag != W("w:pPr"):
                p.remove(c)
        ppr = p.find(W("w:pPr"))
        if ppr is not None:
            rpr = ppr.find(W("w:rPr"))
            if rpr is not None:
                for tag in ("w:i", "w:iCs", "w:u"):
                    for x in rpr.findall(W(tag)):
                        rpr.remove(x)
        return p

    def _rpr(self, bold=False, italic=False, code=False, size=None, color=None, underline=False, highlight=None):
        rpr = etree.Element(W("w:rPr"))
        f = MONO if code else FONT
        rpr.append(el("w:rFonts", ascii=f, cs=f, eastAsia=f, hAnsi=f))
        if bold:
            rpr.append(el("w:b", val=1))
            rpr.append(el("w:bCs", val=1))
        if italic:
            rpr.append(el("w:i", val=1))
            rpr.append(el("w:iCs", val=1))
        if color:
            rpr.append(el("w:color", val=color))
        if size or code:
            hs = int(round(2 * (size or 9.5)))
            rpr.append(el("w:sz", val=hs))
            rpr.append(el("w:szCs", val=hs))
        if highlight:
            rpr.append(el("w:highlight", val=highlight))
        if underline:
            rpr.append(el("w:u", val="single"))
        return rpr

    def _run(self, text, **kw):
        r = etree.Element(W("w:r"))
        r.append(self._rpr(**kw))
        parts = text.split("\n")
        for k, part in enumerate(parts):
            if k:
                etree.SubElement(r, W("w:br"))
            t = etree.SubElement(r, W("w:t"))
            t.text = part
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return r

    INLINE_RE = re.compile(r"(`[^`]+`|\*\*.+?\*\*|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*])|\[@[^\]]+\]|"
                           r"\[[^\]]+\]\([^)\s]+\)|@(?:fig|tab):[\w\-]+)")

    def cite(self, keys: list) -> str:
        nums = []
        for k in keys:
            if k not in self.refs:
                raise BuildError(f"citation [@{k}] has no verified entry in report/references.json")
            if k not in self.cite_order:
                self.cite_order.append(k)
            nums.append(self.cite_order.index(k) + 1)
        return "[" + ", ".join(str(n) for n in nums) + "]"

    def add_inline(self, p, text, size=None, base_bold=False, base_italic=False, highlight=None):
        pos = 0
        for m in self.INLINE_RE.finditer(text):
            if m.start() > pos:
                p.append(self._run(text[pos:m.start()], size=size, bold=base_bold, italic=base_italic, highlight=highlight))
            tok = m.group(0)
            if tok.startswith("`"):
                p.append(self._run(tok[1:-1], code=True, size=(size - 1.5) if size else 9.5, highlight=highlight))
            elif tok.startswith("**"):
                self.add_inline(p, tok[2:-2], size=size, base_bold=True, base_italic=base_italic, highlight=highlight)
            elif tok.startswith("*"):
                self.add_inline(p, tok[1:-1], size=size, base_bold=base_bold, base_italic=True, highlight=highlight)
            elif tok.startswith("[@"):
                keys = [k.strip().lstrip("@") for k in tok[2:-1].split(";")]
                p.append(self._run(self.cite(keys), size=size, bold=base_bold, italic=base_italic, highlight=highlight))
            elif tok.startswith("@"):
                lab = tok[1:]
                if lab not in self.labels:
                    raise BuildError(f"unknown label {tok} (labels: {sorted(self.labels)})")
                p.append(self._run(self.labels[lab], size=size, bold=base_bold, italic=base_italic, highlight=highlight))
            else:
                mm = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)", tok)
                self.link(p, mm.group(1), mm.group(2), size=size)
            pos = m.end()
        if pos < len(text):
            p.append(self._run(text[pos:], size=size, bold=base_bold, italic=base_italic, highlight=highlight))

    def link(self, p, label, url, size=None):
        rid = self.doc.part.relate_to(url, RT.HYPERLINK, is_external=True)
        h = etree.SubElement(p, W("w:hyperlink"))
        h.set(qn("r:id"), rid)
        h.append(self._run(label, color=LINK_COLOR, underline=True, size=size))

    def new_num(self, proto_key) -> str:
        """A fresh w:num on the template's abstract list, so each list restarts at 1."""
        old = self.protos[proto_key].find(W("w:pPr")).find(W("w:numPr")).find(W("w:numId")).get(W("w:val"))
        abs_id = None
        for n in self.numbering.findall(W("w:num")):
            if n.get(W("w:numId")) == old:
                abs_id = n.find(W("w:abstractNumId")).get(W("w:val"))
        ids = [int(n.get(W("w:numId"))) for n in self.numbering.findall(W("w:num"))]
        new_id = str(max(ids) + 1)
        num = el("w:num", numId=new_id)
        num.append(el("w:abstractNumId", val=abs_id))
        ov = el("w:lvlOverride", ilvl=0)
        ov.append(el("w:startOverride", val=1))
        num.append(ov)
        self.numbering.append(num)
        return new_id

    # ---------------------------------------------------------------- blocks
    def heading(self, level, text):
        p = copy.deepcopy(self.protos["h2" if level == 1 else "h3"])
        runs = p.findall(W("w:r"))
        rpr = copy.deepcopy(runs[0].find(W("w:rPr")))
        for r in runs:
            p.remove(r)
        r = etree.SubElement(p, W("w:r"))
        r.append(rpr)
        t = etree.SubElement(r, W("w:t"))
        t.text = text
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        self._append(p)

    def paragraph(self, text, size=None, spacer=True):
        p = self._new_from("body")
        ppr_set(p.find(W("w:pPr")), "spacing", after=AFTER if spacer else 40)
        hl = None
        if text.startswith("TODO"):
            self.todo += 1
            hl = "yellow"
        self.add_inline(p, text, size=size, highlight=hl)
        self._append(p)

    def lst(self, kind, items, proto=None):
        proto = proto or ("ol" if kind == "ol" else "ul")
        num_id = self.new_num(proto)
        for k, it in enumerate(items):
            p = self._new_from(proto)
            ppr = p.find(W("w:pPr"))
            ppr.find(W("w:numPr")).find(W("w:numId")).set(W("w:val"), num_id)
            ppr_set(ppr, "spacing", after=AFTER if k == len(items) - 1 else 40)
            self.add_inline(p, it)
            self._append(p)

    def code(self, text, size=7.5):
        p = self._new_from("body")
        ppr_set(p.find(W("w:pPr")), "spacing", after=AFTER, line=240, lineRule="auto")
        p.append(self._run(text, code=True, size=size))
        self._append(p)

    def caption(self, label, text, after=True):
        p = self._new_from("body")
        ppr = p.find(W("w:pPr"))
        ppr_set(ppr, "spacing", before=60, after=AFTER if after else 60, line=240, lineRule="auto")
        ppr_set(ppr, "jc", val="both")
        p.append(self._run(label + ". ", bold=True, size=9.5))
        self.add_inline(p, text, size=9.5)
        return p

    def register_labels(self, blocks):
        nf = nt = 0
        for b in blocks:
            if b["t"] == "fig":
                nf += 1
                if b["attrs"].get("id"):
                    self.labels[b["attrs"]["id"]] = f"Figure {nf}"
            elif b["t"] in ("table", "csv"):
                nt += 1
                if b["attrs"].get("id"):
                    self.labels[b["attrs"]["id"]] = f"Table {nt}"

    def _image_para(self, rel: str, width: float):
        path = (REPO / rel).resolve()
        if not path.exists():
            raise BuildError(f"figure not found: {rel}")
        if path.suffix.lower() == ".pdf":
            import pymupdf
            d = pymupdf.open(path)
            png = OUT_TMP / (path.stem + ".png")
            d[0].get_pixmap(dpi=300).save(png)
            path = png
        tmp = self.doc.add_paragraph()   # python-docx handles the image part and relationship
        tmp.add_run().add_picture(str(path), width=Inches(width))
        pe = tmp._p
        ppr = pe.get_or_add_pPr()
        ppr_set(ppr, "keepNext", val=1)
        ppr_set(ppr, "spacing", before=60, after=0)
        ppr_set(ppr, "jc", val="center")
        pe.getparent().remove(pe)
        return pe

    def figure(self, b):
        """One figure; several comma-separated paths are placed side by side as panels (a), (b)."""
        self.n_fig += 1
        paths = [x.strip() for x in b["path"].split(",") if x.strip()]
        if len(paths) == 1:
            self._append(self._image_para(paths[0], float(b["attrs"].get("width", 6.0))))
        else:
            width = float(b["attrs"].get("width", 6.4 / len(paths) - 0.1))
            col = 9360 // len(paths)
            tbl = etree.Element(W("w:tbl"))
            tpr = etree.SubElement(tbl, W("w:tblPr"))
            tpr.append(el("w:tblW", w=9360, type="dxa"))
            tpr.append(el("w:jc", val="center"))
            bd = etree.SubElement(tpr, W("w:tblBorders"))
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                bd.append(el(f"w:{side}", val="nil"))
            tpr.append(el("w:tblLayout", type="fixed"))
            grid = etree.SubElement(tbl, W("w:tblGrid"))
            for _ in paths:
                grid.append(el("w:gridCol", w=col))
            tr = etree.SubElement(tbl, W("w:tr"))
            trpr = etree.SubElement(tr, W("w:trPr"))
            trpr.append(el("w:cantSplit", val=1))
            for k, rel in enumerate(paths):
                tc = etree.SubElement(tr, W("w:tc"))
                tcpr = etree.SubElement(tc, W("w:tcPr"))
                tcpr.append(el("w:tcW", w=col, type="dxa"))
                tc.append(self._image_para(rel, width))
                lab = etree.SubElement(tc, W("w:p"))
                lppr = etree.SubElement(lab, W("w:pPr"))
                ppr_set(lppr, "keepNext", val=1)
                ppr_set(lppr, "spacing", before=0, after=0)
                ppr_set(lppr, "jc", val="center")
                lab.append(self._run(f"({chr(97 + k)})", size=9.5))
            self._append(tbl)
        self._append(self.caption(f"Figure {self.n_fig}", b["caption"]))

    def table(self, caption, rows, header=True, size=8.5, aligns=None):
        self.n_tab += 1
        cap = self.caption(f"Table {self.n_tab}", caption, after=False)
        ppr_set(cap.find(W("w:pPr")), "keepNext", val=1)
        self._append(cap)
        ncol = max(len(r) for r in rows)
        plain = lambda t: re.sub(r"[`*]|\[@[^\]]*\]", "", t)
        lens = [max(len(plain(r[c])) if c < len(r) else 0 for r in rows) for c in range(ncol)]
        weights = [min(max(L, 4), 70) for L in lens]
        widths = [int(9360 * w / sum(weights)) for w in weights]
        if aligns is None:
            body_lens = [max((len(plain(r[c])) if c < len(r) else 0) for r in rows[1:]) if len(rows) > 1 else 0
                         for c in range(ncol)]
            aligns = ["left" if (c == 0 or body_lens[c] > 14) else "center" for c in range(ncol)]
        tbl = etree.Element(W("w:tbl"))
        tpr = etree.SubElement(tbl, W("w:tblPr"))
        tpr.append(el("w:tblW", w=9360, type="dxa"))
        tpr.append(el("w:jc", val="center"))
        borders = etree.SubElement(tpr, W("w:tblBorders"))
        for side in ("top", "bottom"):
            borders.append(el(f"w:{side}", val="single", sz=8, space=0, color="000000"))
        for side in ("left", "right", "insideH", "insideV"):
            borders.append(el(f"w:{side}", val="nil"))
        tpr.append(el("w:tblLayout", type="fixed"))
        mar = etree.SubElement(tpr, W("w:tblCellMar"))
        mar.append(el("w:left", w=60, type="dxa"))
        mar.append(el("w:right", w=60, type="dxa"))
        grid = etree.SubElement(tbl, W("w:tblGrid"))
        for wd in widths:
            grid.append(el("w:gridCol", w=wd))
        for ri, row in enumerate(rows):
            tr = etree.SubElement(tbl, W("w:tr"))
            trpr = etree.SubElement(tr, W("w:trPr"))
            trpr.append(el("w:cantSplit", val=1))
            if ri == 0 and header:
                trpr.append(el("w:tblHeader", val=1))
            for ci in range(ncol):
                txt = row[ci] if ci < len(row) else ""
                tc = etree.SubElement(tr, W("w:tc"))
                tcpr = etree.SubElement(tc, W("w:tcPr"))
                tcpr.append(el("w:tcW", w=widths[ci], type="dxa"))
                if ri == 0 and header:
                    b = etree.SubElement(tcpr, W("w:tcBorders"))
                    b.append(el("w:bottom", val="single", sz=4, space=0, color="000000"))
                p = etree.SubElement(tc, W("w:p"))
                ppr = etree.SubElement(p, W("w:pPr"))
                ppr_set(ppr, "spacing", before=20, after=20, line=240, lineRule="auto")
                ppr_set(ppr, "jc", val=aligns[ci])
                self.add_inline(p, txt, size=size, base_bold=(ri == 0 and header))
        self._append(tbl)
        self.spacer()

    def csv_table(self, b):
        a = b["attrs"]
        rel = b["path"].replace("{grid}", self.tables.grid)
        p = REPO / rel
        if not p.exists():
            raise BuildError(f"csv table source not found: {rel}")
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
        grid_name = Path(rel).parent.name
        aliases = self.tables.models(grid_name)
        where = {}
        for cond in (a.get("where") or "").split("&"):
            if cond:
                k, v = cond.split("=", 1)
                where[k] = v
        sel = [r for r in rows if all(self.tables._match(r.get(k, ""), v, aliases if k in ("model", "agents") else None)
                                      for k, v in where.items())]
        if not sel:
            raise BuildError(f"!csv {rel}: no rows match {where}")
        cols = []
        for spec in a["cols"].split(","):
            parts = spec.split(":")
            cols.append((parts[0], parts[1] if len(parts) > 1 else parts[0], parts[2] if len(parts) > 2 else None))
        inv = {v: k for k, v in aliases.items()}
        out = [[h.replace("_", " ") for _, h, _ in cols]]
        for r in sel:
            line = []
            for c, _, f in cols:
                if c == "model" and r.get(c) in inv:
                    txt = f"{inv[r[c]]}"
                else:
                    txt, _ = self.tables._metric([r], c, f, None, f"!csv {rel}", Path(rel).name)
                line.append(txt)
                self.tables.log.append({"placeholder": f"!csv col={c}", "file": rel, "sha256": sha,
                                        "rows": ",".join(f"{k}={r.get(k)}" for k in list(where) + ["model", "shape", "condition"] if k in r),
                                        "raw": r.get(c, ""), "text": txt})
            out.append(line)
        self.table(b["caption"], out, size=float(a.get("size", 8)))

    def prompts(self):
        from dst.agents import prompts as P
        from dst.env import ROLE_TOOLS
        self.paragraph("The texts below are printed from `dst/agents/prompts.py` at build time. Each agent's system "
                       "prompt is its role text, an identity line, its tool list, the condition's additions and the "
                       "format rules, in that order.")
        for role in ("lead", "manager", "worker"):
            obs = {"lead": {"agent": "lead", "role": "lead", "parent": None,
                            "children": ["w1", "w2", "w3", "w4", "w5", "w6"], "task_id": "batch-1"},
                   "manager": {"agent": "m1", "role": "manager", "parent": "lead",
                               "children": ["w1", "w2", "w3"], "task_id": "batch-1"},
                   "worker": {"agent": "w1", "role": "worker", "parent": "lead", "children": [],
                              "task_id": "batch-1"}}[role]
            ident = P.identity_line(obs)
            text = P.system_prompt(role, "B0", ROLE_TOOLS[role], identity=ident)
            self.paragraph(f"**System prompt, {role}, condition B0** (shape F for the lead and worker examples, shape D for the manager).", spacer=False)
            self.code(text)
        self.paragraph("**Added in P1** (after the tool list and before the format rules):", spacer=False)
        self.code(P.DISCIPLINE)
        self.paragraph("**Added in E1 and E3** (API documentation of the idempotency key; the control is enforced by "
                       "the environment):", spacer=False)
        self.code(P.ENV_NOTES["idempotency"])
        self.paragraph("**Added in E2 and E3:**", spacer=False)
        self.code(P.ENV_NOTES["readback_gate"])
        self.paragraph("**Format feedback** (sent at the start of the next turn after an unparseable reply, or a reply "
                       "with more than one call):", spacer=False)
        from dst.agents.llm_agent import MULTI_FEEDBACK, PARSE_FEEDBACK
        self.code(PARSE_FEEDBACK + "\n" + MULTI_FEEDBACK.replace("{n}", "<n>"))

    def write(self, blocks):
        self.register_labels(blocks)
        self._ref_num = None
        for b in blocks:
            t = b["t"]
            if t == "h":
                self.heading(b["level"], b["text"])
            elif t == "p":
                self.paragraph(b["text"])
            elif t in ("ul", "ol"):
                self.lst(t, b["items"])
            elif t == "code":
                self.code(b["text"])
            elif t == "fig":
                self.figure(b)
            elif t == "table":
                        self.table(b["caption"], b["rows"])
            elif t == "csv":
                self.csv_table(b)
            elif t == "pagebreak":
                p = etree.Element(W("w:p"))
                r = etree.SubElement(p, W("w:r"))
                r.append(el("w:br", type="page"))
                self._append(p)
            elif t == "prompts":
                self.prompts()
            elif t == "references":
                self._ref_num = self.new_num("ref")
                self._refs_anchor = etree.Element(W("w:p"))
                self._append(self._refs_anchor)
        # the reference list is written last (citations later in the text count too), at its anchor
        if self._ref_num is not None:
            anchor = self._refs_anchor
            saved = self.sect
            items = []
            for k in self.cite_order:
                p = self._new_from("ref")
                p.find(W("w:pPr")).find(W("w:numPr")).find(W("w:numId")).set(W("w:val"), self._ref_num)
                self.add_inline(p, format_reference(self.refs[k]))
                items.append(p)
            for p in items:
                anchor.addprevious(p)
            anchor.getparent().remove(anchor)
            self.sect = saved
        elif self.cite_order:
            raise BuildError("citations used but no !references block")


def format_reference(r: dict) -> str:
    authors = r.get("cite_authors") or r.get("authors") or []
    if len(authors) > 5:
        a = f"{authors[0]} et al."
    elif len(authors) > 1:
        a = ", ".join(authors[:-1]) + " and " + authors[-1]
    else:
        a = authors[0] if authors else ""
    year = r.get("cite_year") or r.get("year") or "n.d."
    title = (r.get("cite_title") or r["title"]).replace("$", "")
    venue = r.get("cite_venue")
    url = r.get("cite_url") or r["url"]
    if not venue:
        m = re.search(r"arxiv\.org/abs/([\d.]+)", url)
        venue = f"arXiv:{m.group(1)}" if m else ""
    s = f"{a} ({year}). {title}" + ("" if title.endswith(("?", "!", ".")) else ".")
    if venue:
        s += f" {venue}."
    s += f" [{url}]({url})"
    if r.get("cite_accessed"):
        s += f" (accessed {r['cite_accessed']})"
    return s


# =========================================================================================
# PDF conversion and checks
# =========================================================================================

def to_pdf(docx_path: Path, out_dir: Path) -> Path:
    import fcntl
    profile = Path("/tmp/dst-report-lo-profile")
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)     # one conversion at a time on this host
        cmd = ["nice", "-n", "10", "timeout", "300", "soffice", "--headless", "--norestore",
               f"-env:UserInstallation=file://{profile}", "--convert-to", "pdf", "--outdir", str(out_dir),
               str(docx_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        # make sure no office process of ours is left behind
        subprocess.run(["pkill", "-f", f"UserInstallation=file://{profile}"], capture_output=True)
    pdf = out_dir / (docx_path.stem + ".pdf")
    if res.returncode != 0 or not pdf.exists():
        raise BuildError(f"LibreOffice conversion failed ({res.returncode}): {res.stderr[-500:]}")
    return pdf


def pdf_report(pdf: Path) -> dict:
    import pymupdf
    d = pymupdf.open(pdf)
    ref_page = ref_y = None
    headings = {}
    full = []
    for i, page in enumerate(d):
        full.append(page.get_text())
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                txt = "".join(s["text"] for s in line["spans"]).strip()
                if not line["spans"]:
                    continue
                size = max(s["size"] for s in line["spans"])
                bold = any("Bold" in s["font"] for s in line["spans"])
                if size >= 13 and bold and txt:
                    headings.setdefault(txt, (i + 1, line["bbox"][1] / page.rect.height))
    if "References" in headings:
        ref_page, ref_y = headings["References"]
    return {"pages": d.page_count, "ref_page": ref_page, "ref_y": ref_y, "text": "\n".join(full),
            "headings": headings}


def rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def word_count(text: str) -> int:
    t = re.sub(r"\[@[^\]]+\]", "", text)
    t = re.sub(r"[*`]", "", t)
    return len(re.findall(r"\S+", t))


OUT_TMP = Path("/tmp")


def main(argv=None) -> int:
    global OUT_TMP
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=str(HERE / "report.md"))
    ap.add_argument("--grid", required=True, help="results/<grid>/ and figures/<grid>/ (e.g. grid, scripted-demo)")
    ap.add_argument("--demo", action="store_true", help="pipeline test build, marked as not the submission")
    ap.add_argument("--final", action="store_true", help="fail on TODO, > 8 body pages, abstract outside 150-250 words")
    ap.add_argument("--out", default=None, help="output dir (default report/out, or report/out/demo with --demo)")
    ap.add_argument("--banned-file", default=os.environ.get("DST_REPORT_BANNED"),
                    help="file of regular expressions (one per line) that must not occur in the PDF text")
    a = ap.parse_args(argv)
    if a.banned_file:
        bf = Path(a.banned_file).expanduser()
        if not bf.exists():
            raise SystemExit(f"--banned-file not found: {bf}")
        BANNED[:] = [ln.strip() for ln in bf.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.startswith("#")]
    if a.demo and a.final:
        raise SystemExit("--demo and --final exclude each other")
    out = Path(a.out) if a.out else (HERE / "out" / ("demo" if a.demo else ""))
    out.mkdir(parents=True, exist_ok=True)
    OUT_TMP = out
    stem = "report-demo" if a.demo else "report"
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    refs = {r["key"]: r for r in json.loads((HERE / "references.json").read_text(encoding="utf-8")) if r.get("verified")}

    src = Path(a.source)
    text = preprocess(src.read_text(encoding="utf-8"), a.demo, src.parent)
    text = text.replace("{grid}", a.grid)
    tables = Tables(a.grid, config)
    try:
        text = resolve_placeholders(text, tables)
        meta, body = parse_front(text)
        for k in ("title", "author", "affiliation"):
            if not meta.get(k):
                raise BuildError(f"front matter needs {k}:")
        blocks = parse_blocks(body)
        # split off the abstract
        abstract, rest, cur = [], [], None
        for b in blocks:
            if b["t"] == "h" and b["level"] == 1:
                cur = "abstract" if b["text"].lower() == "abstract" else "body"
                if cur == "abstract":
                    continue
            (abstract if cur == "abstract" else rest).append(b)
        if not abstract:
            raise BuildError("no # Abstract section")
        abstract_text = " ".join(b["text"] for b in abstract if b["t"] == "p")
        words = word_count(abstract_text)

        d = docx.Document(str(TEMPLATE))
        wr = Writer(d, refs, a.demo, tables)
        wr.take_prototypes()
        wr.fill_title_block(meta, abstract)
        wr.fix_footnote()
        if a.demo:
            wr.demo_banner()
        wr.write(rest)
        todo = wr.todo + (1 if abstract_text.startswith("TODO") else 0)
        docx_path = out / f"{stem}.docx"
        d.save(str(docx_path))
        # numbers log
        with open(out / "numbers.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["placeholder", "file", "sha256", "rows", "raw", "text"])
            w.writeheader()
            for row in tables.log:
                w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in row.items()})
        pdf = to_pdf(docx_path, out)
        rep = pdf_report(pdf)
    except BuildError as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        return 1

    problems, warnings = [], []
    for g in GUIDANCE:
        if g in rep["text"]:
            problems.append(f"template guidance left in the PDF: {g!r}")
    for pat in BANNED:
        for mm in re.finditer(pat, rep["text"], flags=re.I):
            ctx = rep["text"][max(0, mm.start() - 40): mm.end() + 40].replace("\n", " ")
            problems.append(f"banned word {mm.group(0)!r}: ...{ctx}...")
    size = pdf.stat().st_size
    if size >= 25 * 1024 * 1024:
        problems.append(f"PDF is {size / 1e6:.1f} MB (limit 25 MB)")
    body_pages = None
    if rep["ref_page"] is None:
        warnings.append("no 'References' heading found in the PDF")
    else:
        body_pages = rep["ref_page"] - (1 if rep["ref_y"] < 0.12 else 0)
        if body_pages > 8:
            (problems if a.final else warnings).append(f"{body_pages} pages before References (limit 8)")
    if not 150 <= words <= 250:
        (problems if a.final else warnings).append(f"abstract has {words} words (150-250)")
    if todo:
        (problems if a.final else warnings).append(f"{todo} TODO paragraph(s) left")
    if tables.pending:
        (problems if a.final else warnings).append(
            f"{len(tables.pending)} monitor placeholder(s) print [monitor pending], run not finished: "
            + "; ".join(sorted({f"results/{f} ({why})" for _, f, why in tables.pending})))

    print(f"{'DEMO build (not the submission)' if a.demo else 'build'}: grid={a.grid}")
    print(f"  docx: {rel(docx_path)}")
    print(f"  pdf:  {rel(pdf)}  {size / 1024:.0f} KB ({size / 1e6:.2f} MB)")
    if rep["ref_page"]:
        print(f"  pages before References: {body_pages} (References heading on page {rep['ref_page']}, "
              f"{100 * rep['ref_y']:.0f}% down the page); total pages: {rep['pages']}")
    else:
        print(f"  total pages: {rep['pages']}")
    print(f"  abstract: {words} words")
    print(f"  placeholders resolved: {sum(1 for r in tables.log if r['placeholder'].startswith('{{'))}, "
          f"csv table cells: {sum(1 for r in tables.log if r['placeholder'].startswith('!csv'))} "
          f"(log: {rel((out / 'numbers.csv'))})")
    print(f"  references cited: {len(wr.cite_order)} of {len(refs)} verified; banned-word patterns checked: {len(BANNED)}")
    for w_ in warnings:
        print(f"  WARNING: {w_}")
    for p_ in problems:
        print(f"  ERROR: {p_}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
