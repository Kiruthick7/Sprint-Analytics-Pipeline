"""Jira verification violations: doughnut chart, stacked bar, and summary table."""
import base64
import io
import logging

logger = logging.getLogger(__name__)

VIOLATION_LABELS = [
    "Missing +1 AC confirmation comment from developer",
    "Missing PR link (Code Review step)",
    "No time logged (Post-Merge step)",
    "No dev/local evidence attached (Post-Merge step)",
    "Branch Cleanup not mentioned",
    "Concourse link not pasted in Jira (concourse-ci.hy-vee.cloud/builds)",
    "Rollback strategy not mentioned",
]
VIOLATION_SHORT = [
    "Missing +1 AC comment",
    "Missing PR link",
    "No time logged",
    "No dev/local evidence",
    "Branch Cleanup not mentioned",
    "Concourse link not pasted",
    "Rollback strategy not mentioned",
]

_BAR_CHARS = ("█", "▓", "▒", "░", "□")
_STACKED_BAR_WIDTH = 48


def aggregate_violation_counts(violations: list) -> dict:
    """Return counts per violation type (key = full label, value = count)."""
    counts = {label: 0 for label in VIOLATION_LABELS}
    for v in violations:
        for violation_text in v.get("violations") or []:
            if violation_text in counts:
                counts[violation_text] += 1
    return counts


def _violations_doughnut_chart_base64(counts: dict, size_inches: float = 6.5) -> str | None:
    """Generate a doughnut chart for violation counts; return base64 PNG or None if matplotlib unavailable."""
    total = sum(counts.get(l, 0) for l in VIOLATION_LABELS)
    if total == 0:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle
        import matplotlib.patheffects as path_effects
    except ImportError:
        logger.debug("matplotlib not available for violations chart")
        return None
    labels_list = []
    sizes_list = []
    for i, label in enumerate(VIOLATION_LABELS):
        c = counts.get(label, 0)
        if c == 0:
            continue
        labels_list.append(VIOLATION_SHORT[i])
        sizes_list.append(c)
    if not sizes_list:
        return None
    palette = ["#2563eb", "#7c3aed", "#059669", "#ea580c", "#dc2626", "#0891b2", "#ca8a04"]
    colors = [palette[i % len(palette)] for i in range(len(labels_list))]
    fig, ax = plt.subplots(figsize=(size_inches, size_inches))
    fig.patch.set_facecolor("none")
    ax.set_facecolor("none")
    pie_result = ax.pie(
        sizes_list,
        labels=None,
        autopct="%1.0f%%",
        startangle=90,
        colors=colors,
        pctdistance=0.75,
        wedgeprops=dict(width=0.55, edgecolor="white", linewidth=1.2),
    )
    wedges = pie_result[0]
    autotexts = pie_result[2] if len(pie_result) > 2 else []
    for t in autotexts:
        t.set_fontsize(9)
        t.set_color("white")
        t.set_fontweight("bold")
    centre_circle = Circle((0, 0), 0.45, facecolor="none", edgecolor="none")
    ax.add_artist(centre_circle)
    centre_text = ax.text(0, 0, f"Total\n{total}", ha="center", va="center", fontsize=11, weight="bold", color="white")
    centre_text.set_path_effects([path_effects.withStroke(foreground="black", linewidth=2)])
    leg = ax.legend(wedges, labels_list, loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=10, labelcolor="white", framealpha=0.9)
    for text in leg.get_texts():
        text.set_path_effects([path_effects.withStroke(foreground="black", linewidth=2)])
        text.set_fontweight("bold")
    ax.axis("equal")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="none", edgecolor="none", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _violations_stacked_bar(counts: dict) -> str:
    """One-line stacked horizontal bar for Confluence when matplotlib is unavailable."""
    total = sum(counts.get(l, 0) for l in VIOLATION_LABELS)
    if total == 0:
        return ""
    rows = []
    for i, label in enumerate(VIOLATION_LABELS):
        c = counts.get(label, 0)
        if c == 0:
            continue
        pct = c / total
        short = VIOLATION_SHORT[i]
        rows.append((short, c, round(100 * pct), pct))
    rows.sort(key=lambda x: -x[1])
    width_used = 0
    seg_widths = []
    for short, c, pct, pct_float in rows:
        w = max(1, round(_STACKED_BAR_WIDTH * pct_float))
        seg_widths.append((short, c, pct, w))
        width_used += w
    if width_used > _STACKED_BAR_WIDTH:
        seg_widths[-1] = (seg_widths[-1][0], seg_widths[-1][1], seg_widths[-1][2], max(1, seg_widths[-1][3] - (width_used - _STACKED_BAR_WIDTH)))
    bar_parts = [_BAR_CHARS[i] * w for i, (_, _, _, w) in enumerate(seg_widths)]
    bar_line = "".join(bar_parts)[:_STACKED_BAR_WIDTH].ljust(_STACKED_BAR_WIDTH)
    legend = " · ".join(f"{_BAR_CHARS[i]} {short} ({c}, {pct}%)" for i, (short, c, pct, _) in enumerate(seg_widths))
    return f"`{bar_line}`\n\n*{legend}*\n\n"


def violations_summary_table(counts: dict) -> str:
    """Violation counts: doughnut chart (when matplotlib available) or stacked bar + table."""
    total = sum(counts.get(l, 0) for l in VIOLATION_LABELS)
    if total == 0:
        return "*No verification violations in this sprint.*\n\n"
    out = ""
    b64 = _violations_doughnut_chart_base64(counts)
    if b64:
        out = (
            f'<p style="margin:0;padding:0"><img src="data:image/png;base64,{b64}" alt="Jira Verification Violations" '
            'style="max-width:520px;height:auto;display:block;vertical-align:top;" /></p>\n'
        )
    else:
        out = _violations_stacked_bar(counts)
    rows = []
    for i, label in enumerate(VIOLATION_LABELS):
        c = counts.get(label, 0)
        if c == 0:
            continue
        pct = round(100 * c / total)
        short = VIOLATION_SHORT[i]
        rows.append((short, c, pct))
    rows.sort(key=lambda x: -x[1])
    lines = [
        "| **Violation type** | **Count** | **% of total** |",
        "|--------------------|-----------|----------------|",
    ]
    for short, c, pct in rows:
        lines.append(f"| {short} | **{c}** | **{pct}%** |")
    return out + "\n".join(lines) + "\n"
