"""Figures from the analysis tables (results/table1.csv, results/validation.csv).

Figures read only the CSVs written by ``analysis.report``; every plotted number is a
number in those tables. Sized for a single column (3.5 in wide), PDF + PNG (300 dpi).

Color: model identity uses categorical slots in fixed order (blue, orange, aqua; validated
for colorblind separation), tree shape uses a one-hue ordinal ramp (light to dark = small to
large tree). Every series also has its own marker, so identity never rests on color alone.
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .tables import CONDITION_ORDER, SHAPE_ORDER  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
MARKERS = ["o", "s", "D", "^", "v", "P", "X", "h"]
SHAPE_RAMP = {"F": "#86b6ef", "D": "#2a78d6", "D+": "#104281"}
SHAPE_MARK = {"F": "o", "D": "s", "D+": "D"}
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e2e1dc"
WIDTH = 3.5
COND_NAMES = {"B0": "B0 none", "P1": "P1 prompt", "E1": "E1 idem.", "E2": "E2 gate", "E3": "E3 both"}


def _style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 7, "axes.titlesize": 7.5, "axes.labelsize": 7,
        "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5, "axes.edgecolor": INK2,
        "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID,
        "grid.linewidth": 0.6, "grid.linestyle": "-", "axes.axisbelow": True, "figure.dpi": 100,
        "savefig.dpi": 300, "pdf.fonttype": 42, "legend.frameon": False, "figure.facecolor": "white",
    })


def _save(fig, out: Path, name: str) -> list:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("pdf", "png"):
        p = out / f"{name}.{ext}"
        # fixed metadata so the PDF is reproducible
        meta = {"CreationDate": None, "ModDate": None} if ext == "pdf" else {"Software": None}
        fig.savefig(p, bbox_inches="tight", pad_inches=0.03, metadata=meta)
        paths.append(p)
    plt.close(fig)
    return paths


def _ordered(vals, ref):
    vals = list(dict.fromkeys(vals))
    return sorted(vals, key=lambda x: (ref.index(x) if x in ref else len(ref), str(x)))


def _short(model) -> str:
    """Display name for a model: drop a provider path prefix ("@cf/ibm-granite/granite-4.0-h-micro" ->
    "granite-4.0-h-micro") so titles and legends fit the column width."""
    return str(model).rsplit("/", 1)[-1]


def _model_style(models):
    return {m: (SERIES[i % len(SERIES)], MARKERS[i % len(MARKERS)]) for i, m in enumerate(models)}


def _errdot(ax, x, y, lo, hi, color, marker, label=None, ms=4.2):
    yerr = [[max(0.0, y - lo) if not math.isnan(lo) else 0.0], [max(0.0, hi - y) if not math.isnan(hi) else 0.0]]
    ax.errorbar([x], [y], yerr=yerr, fmt=marker, color=color, ecolor=color, elinewidth=1.1, capsize=0, ms=ms,
                mec="white", mew=0.8, label=label, zorder=3)


def _profiles(t1):
    return sorted(t1["faults"].unique())


def _suffix(profile, profiles):
    return "" if len(profiles) == 1 else "_" + "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in profile)


def _note(fig, text, width=88):
    import textwrap
    lines = textwrap.wrap(" ".join(text.split()), width)
    fig.text(0.0, -0.01, "\n".join(lines), ha="left", va="top", fontsize=5.8, color=INK2)


def fig1(t1: pd.DataFrame, out: Path, label: str) -> list:
    """DAR per 100 and worker / lead FCR by condition, one series per model, shapes pooled."""
    paths = []
    profiles = _profiles(t1)
    for prof in profiles:
        d = t1[(t1["shape"] == "all") & (t1.faults == prof)]
        models = _ordered(d.model, [])
        conds = _ordered(d.condition, CONDITION_ORDER)
        sty = _model_style(models)
        panels = [("dar_per100", "Duplicates per 100\nrequired effects", 1.0),
                  ("fcr_worker", "Worker false dones (%)", 100.0),
                  ("fcr_lead", "Lead false dones (%)", 100.0)]
        fig, axes = plt.subplots(len(panels), 1, figsize=(WIDTH, 4.6), sharex=True)
        off = 0.22 if len(models) > 1 else 0.0
        for ax, (col, ylab, s) in zip(axes, panels):
            lo_c, hi_c = (col + "_lo", col + "_hi")
            for i, m in enumerate(models):
                dm = d[d.model == m].set_index("condition")
                for j, c in enumerate(conds):
                    if c not in dm.index or pd.isna(dm.loc[c, col]):
                        continue
                    r = dm.loc[c]
                    x = j + (i - (len(models) - 1) / 2) * off
                    _errdot(ax, x, s * r[col], s * r[lo_c], s * r[hi_c], *sty[m],
                            label=_short(m) if (col == "dar_per100" and j == 0) else None)
            ax.set_ylabel(ylab)
            ax.set_ylim(bottom=-0.04 * max(1.0, ax.get_ylim()[1]))
        axes[-1].set_xticks(range(len(conds)), [COND_NAMES.get(c, c) for c in conds])
        axes[-1].set_xlim(-0.5, len(conds) - 0.5)
        axes[0].legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=min(3, len(models)), handletextpad=0.3,
                       columnspacing=1.0, borderaxespad=0)
        axes[0].set_title("Failures by condition", loc="left", pad=16)
        _note(fig, f"{label}; faults {prof}. Pooled over shapes; bars: 95% CI\n(DAR: seed bootstrap; FCR: Wilson).")
        fig.tight_layout(h_pad=0.6)
        paths += _save(fig, out, "fig1_failures_by_condition" + _suffix(prof, profiles))
    return paths


def _label_points(ax, pts, multi=False):
    """Label condition names next to points; points at the same spot share one label
    (with the model named when several models sit there)."""
    spots = {}
    for x, y, cond, model in pts:
        spots.setdefault((round(x, 2), round(y, 2)), []).append((model, cond))
    for (x, y), items in spots.items():
        models = list(dict.fromkeys(m for m, _ in items))
        if multi and len(models) > 1:
            text = "; ".join(_short(m).replace("scripted-", "") + " " + ",".join(c for mm, c in items if mm == m) for m in models)
        else:
            text = ",".join(dict.fromkeys(c for _, c in items))
        ax.annotate(text, (x, y), xytext=(4, 3), textcoords="offset points", fontsize=5.8, color=INK2)


def fig2(t1: pd.DataFrame, out: Path, label: str) -> list:
    """Cost overhead vs failure reduction: one point per (model, control), shapes pooled."""
    paths = []
    profiles = _profiles(t1)
    for prof in profiles:
        d = t1[(t1["shape"] == "all") & (t1.faults == prof)]
        models = _ordered(d.model, [])
        sty = _model_style(models)
        measures = sorted(d.cost_measure.dropna().unique())
        xlab = "Cost overhead vs B0 (%, " + (" / ".join(measures) if measures else "steps") + " per episode)"
        panels = [("dar_per100_reduction_vs_b0", "Fewer duplicates\n(per 100 effects)", 1.0),
                  ("fcr_all_reduction_vs_b0", "Fewer false dones\n(points of FCR, %)", 100.0)]
        fig, axes = plt.subplots(len(panels), 1, figsize=(WIDTH, 3.9), sharex=True)
        for ax, (col, ylab, s) in zip(axes, panels):
            ax.axhline(0, color=INK2, lw=0.6, zorder=1)
            ax.axvline(0, color=INK2, lw=0.6, zorder=1)
            ax.grid(True, axis="both")
            pts = []
            for mi, m in enumerate(models):
                dm = d[(d.model == m) & (d.condition != "B0")]
                first = True
                for _, r in dm.iterrows():
                    if pd.isna(r[col]) or pd.isna(r["cost_overhead_vs_b0"]):
                        continue
                    x, y = 100 * r["cost_overhead_vs_b0"], s * r[col]
                    ax.plot([x], [y], sty[m][1], color=sty[m][0], ms=max(2.8, 5.6 - 1.6 * mi), mec="white", mew=0.6, zorder=10 + mi,
                            label=_short(m) if (col == panels[0][0] and first) else None)
                    first = False
                    pts.append((x, y, r["condition"], m))
            _label_points(ax, pts, multi=len(models) > 1)
            ax.margins(x=0.12, y=0.18)
            ax.set_ylabel(ylab)
        axes[-1].set_xlabel(xlab)
        axes[0].legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=min(3, len(models)), handletextpad=0.3,
                       borderaxespad=0)
        axes[0].set_title("Cost vs benefit of each control", loc="left", pad=16)
        _note(fig, f"{label}; faults {prof}. One point per control per model vs B0 of the\n"
                   "same model, shapes pooled. Up = fewer failures, right = more cost.")
        fig.tight_layout(h_pad=0.6)
        paths += _save(fig, out, "fig2_cost_vs_reduction" + _suffix(prof, profiles))
    return paths


def fig3(t1: pd.DataFrame, out: Path, label: str) -> list:
    """Failures by tree shape: rows = metric, columns = model, series = shape."""
    paths = []
    profiles = _profiles(t1)
    for prof in profiles:
        d = t1[(t1["shape"] != "all") & (t1.faults == prof)]
        models = _ordered(d.model, [])
        shapes = _ordered(d["shape"], SHAPE_ORDER)
        conds = _ordered(d.condition, CONDITION_ORDER)
        panels = [("dar_per100", "Duplicates per 100\nrequired effects", 1.0), ("fcr_all", "False dones (%)", 100.0)]
        ncol = len(models)
        fig, axes = plt.subplots(len(panels), ncol, figsize=(WIDTH if ncol <= 2 else 1.75 * ncol, 3.9),
                                 sharex=True, sharey="row", squeeze=False)
        off = 0.24 if len(shapes) > 1 else 0.0
        for ci, m in enumerate(models):
            for ri, (col, ylab, s) in enumerate(panels):
                ax = axes[ri][ci]
                dm = d[d.model == m]
                for si, sh in enumerate(shapes):
                    ds = dm[dm["shape"] == sh].set_index("condition")
                    for j, c in enumerate(conds):
                        if c not in ds.index or pd.isna(ds.loc[c, col]):
                            continue
                        r = ds.loc[c]
                        x = j + (si - (len(shapes) - 1) / 2) * off
                        _errdot(ax, x, s * r[col], s * r[col + "_lo"], s * r[col + "_hi"],
                                SHAPE_RAMP.get(sh, SERIES[si]), SHAPE_MARK.get(sh, MARKERS[si]),
                                label=(f"shape {sh}" if (ri == 0 and ci == 0 and j == 0) else None), ms=3.6)
                if ci == 0:
                    ax.set_ylabel(ylab)
                if ri == 0:
                    ax.set_title(_short(m), loc="left")
                ax.set_xticks(range(len(conds)), conds)
                ax.set_xlim(-0.5, len(conds) - 0.5)
        for row in axes:
            top = max(a.get_ylim()[1] for a in row)
            for a in row:
                a.set_ylim(-0.04 * max(1.0, top), top)
        fig.legend(*axes[0][0].get_legend_handles_labels(), loc="upper left", bbox_to_anchor=(0.0, 0.972),
                   ncol=len(shapes), handletextpad=0.3, borderaxespad=0)
        fig.text(0.0, 1.0, "Failures by tree shape", ha="left", va="top", fontsize=7.5)
        dplus = "; D+ = lead, 3 managers x 5" if "D+" in shapes else ""
        _note(fig, f"{label}; faults {prof}. F = lead + 6 workers; D = lead, 2 managers x 3\n"
                   f"workers{dplus}. Bars: 95% CI (DAR: seed bootstrap; FCR: Wilson).")
        fig.tight_layout(h_pad=0.6, w_pad=0.4, rect=(0, 0, 1, 0.925))
        paths += _save(fig, out, "fig3_failures_by_shape" + _suffix(prof, profiles))
    return paths


def _val_rows(v: pd.DataFrame):
    """Rows for the validation dot plot: cells with a nonzero expectation one by one,
    zero-expectation cells grouped (their measured range is shown)."""
    dar, lead = [], []
    nz = v[(v.agents == "scripted-naive") & v.condition.isin(["B0", "P1", "E2"])]
    for _, r in nz.iterrows():
        dar.append({"label": f"naive {r.condition} {r['shape']}", "meas": r.dar_measured_per100,
                    "lo": r.dar_measured_lo, "hi": r.dar_measured_hi, "exp": r.dar_analytic_per100,
                    "exp_sd": r.dar_analytic_sd_per100, "z": r.dar_z})
    for name, sel in [("naive E1/E3", (v.agents == "scripted-naive") & v.condition.isin(["E1", "E3"])),
                      ("careful, all", v.agents == "scripted-careful")]:
        g = v[sel]
        if len(g):
            dar.append({"label": f"{name} ({len(g)} cells)", "meas": g.dar_measured_per100.max(),
                        "lo": g.dar_measured_lo.min(), "hi": g.dar_measured_hi.max(), "exp": 0.0, "exp_sd": 0.0,
                        "z": float("nan")})
    for _, r in v[(v.agents == "scripted-naive") & (v.condition == "E2")].iterrows():
        if not pd.isna(r.lead_fcr_measured):
            lead.append({"label": f"naive E2 {r['shape']}", "meas": 100 * r.lead_fcr_measured,
                         "lo": 100 * r.lead_fcr_lo, "hi": 100 * r.lead_fcr_hi, "exp": 100 * r.lead_fcr_analytic,
                         "exp_sd": float("nan"), "z": r.lead_fcr_z})
    for name, sel, exp in [("naive B0/P1",
                            (v.agents == "scripted-naive") & v.condition.isin(["B0", "P1"]), 100.0),
                           ("careful, all", v.agents == "scripted-careful", 0.0)]:
        g = v[sel & v.lead_fcr_measured.notna()]
        if len(g):
            lead.append({"label": f"{name} ({len(g)} cells)", "meas": 100 * g.lead_fcr_measured.max(),
                         "lo": 100 * g.lead_fcr_lo.min(), "hi": 100 * g.lead_fcr_hi.max(), "exp": exp,
                         "exp_sd": float("nan"), "z": float("nan")})
    return dar, lead


def fig_validation(v: pd.DataFrame, out: Path, label: str) -> list:
    """Scripted-agent validation: measured metric vs the closed-form expectation."""
    paths = []
    for prof in sorted(v.faults.unique()):
        dar, lead = _val_rows(v[v.faults == prof])
        if not dar:
            continue
        hr = [max(1, len(dar)), max(1, len(lead))]
        fig, axes = plt.subplots(2, 1, figsize=(WIDTH, 0.17 * sum(hr) + 1.3), gridspec_kw={"height_ratios": hr})
        for ax, rows, xlab in [(axes[0], dar, "Duplicates per 100 required effects"),
                               (axes[1], lead, "Lead false dones (%)")]:
            ax.grid(True, axis="x")
            ax.grid(False, axis="y")
            for i, r in enumerate(rows):
                y = len(rows) - 1 - i
                if not pd.isna(r["exp_sd"]) and r["exp_sd"] > 0:
                    ax.plot([r["exp"] - 1.96 * r["exp_sd"], r["exp"] + 1.96 * r["exp_sd"]], [y, y], color=GRID, lw=5,
                            solid_capstyle="butt", zorder=1, label="analytic ±1.96 sd" if i == 0 else None)
                ax.plot([r["exp"]], [y], "|", color=INK, ms=9, mew=1.4, zorder=2,
                        label="analytic expectation" if i == 0 else None)
                ax.errorbar([r["meas"]], [y], xerr=[[max(0, r["meas"] - r["lo"])], [max(0, r["hi"] - r["meas"])]],
                            fmt="o", color=SERIES[0], ecolor=SERIES[0], elinewidth=1.1, ms=4, mec="white", mew=0.8,
                            zorder=3, label="measured (95% CI)" if i == 0 else None)
                if not pd.isna(r["z"]):
                    ax.annotate(f"z={r['z']:+.1f}", (1.0, y), xycoords=("axes fraction", "data"), xytext=(2, 0),
                                textcoords="offset points", va="center", fontsize=5.6, color=INK2)
            ax.set_yticks(range(len(rows)), [r["label"] for r in reversed(rows)])
            ax.set_ylim(-0.6, len(rows) - 0.4)
            ax.set_xlabel(xlab)
        fig.legend(*axes[0].get_legend_handles_labels(), loc="upper left", bbox_to_anchor=(0.0, 0.975), ncol=3,
                   handletextpad=0.3, columnspacing=0.8, borderaxespad=0, fontsize=6)
        fig.text(0.0, 1.0, "Metric validation: measured vs closed form", ha="left", va="top", fontsize=7.5)
        _note(fig, f"{label}; faults {prof}. Grouped rows: range of measured values over\n"
                   "their cells. z = (measured - expected) / analytic sd of the total.")
        fig.tight_layout(h_pad=0.8, rect=(0, 0, 1, 0.94))
        paths += _save(fig, out, "fig_validation_scripted" + ("" if v.faults.nunique() == 1 else "_" + prof))
    return paths


def make_all(results: Path, out: Path, label: str | None = None) -> list:
    _style()
    results, out = Path(results), Path(out)
    t1 = pd.read_csv(results / "table1.csv", keep_default_na=True)
    if label is None:
        import json
        s = results / "summary.json"
        label = (json.loads(s.read_text()).get("label") if s.exists() else "") or results.name
    paths = fig1(t1, out, label) + fig2(t1, out, label) + fig3(t1, out, label)
    vp = results / "validation.csv"
    if vp.exists():
        paths += fig_validation(pd.read_csv(vp), out, label)
    return paths
