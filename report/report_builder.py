"""Build team reports and consolidated Confluence/Teams content."""
import base64
import io
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import JIRA_SERVER, APP_BASE_URL, VELOCITY_DASHBOARD_URL
from jira_api.jira_sprint import (
    get_active_sprint_info,
    get_allowed_sprint_info,
    get_last_closed_sprint_id,
    get_sprint_report_spillover,
    get_sprint_total_story_points,
    get_sprint_story_points_from_issues,
    get_spillover_via_jql,
    get_board_story_points_field,
    validate_sprint_name_format,
)
from jira_api.jira_issues import (
    fetch_issues_for_team,
    get_team_members_from_sprint,
    get_sprint_issues_by_status_with_context,
    get_sprint_issue_count,
    get_sprint_backlog_status_count,
    get_sprint_issues_no_story_points_count,
    escape_markdown_table_cell,
)

from report.llm_report import sprint_chain

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


def _fmt_pts(value) -> str | int | float:
    """Format story points for display: int if whole number, else as-is; None/empty -> '—'. Accepts numeric strings."""
    if value is None or value == "":
        return "—"
    try:
        v = float(value) if not isinstance(value, (int, float)) else value
    except (TypeError, ValueError):
        return "—"
    if v == int(v):
        return int(v)
    return v


def _aggregate_violation_counts(violations: list) -> dict:
    """Return counts per violation type (key = full label, value = count)."""
    counts = {label: 0 for label in VIOLATION_LABELS}
    for v in violations:
        for violation_text in v.get("violations") or []:
            if violation_text in counts:
                counts[violation_text] += 1
    return counts


def _violations_doughnut_chart_base64(counts: dict, size_inches: float = 6.5) -> str | None:
    """Generate a doughnut chart for violation counts; return base64 PNG string or None if matplotlib unavailable."""
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


_VIOLATIONS_BAR_CHARS = ("█", "▓", "▒", "░", "□")
_STACKED_BAR_WIDTH = 48


def _violations_stacked_bar(counts: dict) -> str:
    """One-line stacked horizontal bar: each segment = one violation type (proportional). Renders in Confluence."""
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
    bar_parts = [_VIOLATIONS_BAR_CHARS[i] * w for i, (_, _, _, w) in enumerate(seg_widths)]
    bar_line = "".join(bar_parts)[:_STACKED_BAR_WIDTH].ljust(_STACKED_BAR_WIDTH)
    legend = " · ".join(f"{_VIOLATIONS_BAR_CHARS[i]} {short} ({c}, {pct}%)" for i, (short, c, pct, _) in enumerate(seg_widths))
    return f"`{bar_line}`\n\n*{legend}*\n\n"


def _violations_summary_table(counts: dict) -> str:
    """Violation counts: doughnut chart (when matplotlib available) or stacked-bar + table + summary."""
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


def generate_team_report(team_name: str, team_config: dict) -> tuple[str | None, str | None, str, list]:
    """Generate report for a specific team. Returns (report_markdown, error_message, naming_warning, verification_violations)."""
    logger.info(f"Generating report for team: {team_name}")
    try:
        board_id = team_config["board_id"]
        sprint_info = get_active_sprint_info(board_id)
        excluded = team_config.get("excluded_sprint_names") or []
        included = team_config.get("included_sprint_names")
        excluded_set = {s.strip().lower() for s in excluded if s}
        if sprint_info and excluded_set and (sprint_info.get("name") or "").strip().lower() in excluded_set:
            sprint_info = get_allowed_sprint_info(
                board_id,
                excluded_sprint_names=excluded,
                included_sprint_names=included,
            )
            if not sprint_info:
                logger.info(f"Skipping team {team_name}: active sprint was excluded and no allowed sprint found")
                return None, "⏭️ No allowed sprint for this team (excluded sprints only).", "", []

        if not sprint_info:
            logger.warning(f"No active sprint found for team {team_name}")
            return None, "⚠️ No active sprint found for team {team_name}", "", []

        sprint_name = sprint_info.get("name") or "—"
        logger.info(f"Active sprint for {team_name}: {sprint_name}")

        sprint_name_valid, sprint_name_message = validate_sprint_name_format(sprint_name)
        naming_warning = ""
        if not sprint_name_valid:
            naming_warning = f"⚠️ Sprint name '{sprint_name}' does not match PI<X> S<X> Squad_Name. {sprint_name_message}"
            logger.warning(naming_warning)

        issues_markdown, issue_count, verification_violations, total_story_points = fetch_issues_for_team(team_name, team_config, sprint_name)
        if not issues_markdown:
            logger.warning(f"No issues found for team {team_name}")
            return None, f"ℹ️ No issues found for team {team_name} in sprint {sprint_name}", "", []

        backlog_count = get_sprint_backlog_status_count(sprint_info.get("id"), sprint_name)
        sprint_id = sprint_info.get("id")
        backlog_list = get_sprint_issues_by_status_with_context(sprint_id, sprint_name, "BACKLOG")
        on_hold_list = get_sprint_issues_by_status_with_context(sprint_id, sprint_name, "ON HOLD")

        def _issues_context(items: list, empty_msg: str) -> str:
            if not items:
                return empty_msg
            return "\n".join(
                f"- {b['key']}: {b['summary']} | Status: {b['status_name']} | Comments/excerpts: {b['comments_snippet']}"
                for b in items
            )

        backlog_issues_context = _issues_context(backlog_list, "(No issues on Backlog in this sprint.)")
        on_hold_issues_context = _issues_context(on_hold_list, "(No issues on Hold in this sprint.)")

        logger.info(f"Generating AI summary for team {team_name} with {issue_count} issues")
        result = sprint_chain.invoke({
            "team_name": team_name,
            "sprint_name": sprint_name,
            "sprint_features": issues_markdown,
            "backlog_issues_context": backlog_issues_context,
            "on_hold_issues_context": on_hold_issues_context,
        })
        report = result.content if hasattr(result, "content") else str(result)
        report = re.sub(r"\n{3,}", "\n\n", report)

        detected_members = team_config.get("members") or get_team_members_from_sprint(sprint_name, board_id)

        sprint_issue_count = get_sprint_issue_count(sprint_id, sprint_name, use_custom_filter=False)
        total_pts_from_sprint = (
            get_sprint_total_story_points(
                board_id, sprint_id, sprint_name=sprint_name, use_report_jql=True
            )
            if sprint_id is not None
            else None
        )
        total_pts_display = _fmt_pts(total_pts_from_sprint if total_pts_from_sprint is not None else total_story_points)
        if total_pts_display == 0:
            total_pts_display = "—"
        points_breakdown = (
            get_sprint_story_points_from_issues(board_id, sprint_id=sprint_id, sprint_name=sprint_name, use_report_jql=True)
            if sprint_id else {}
        )
        completed_display = _fmt_pts(points_breakdown.get("completed") or 0)
        backlog_display = _fmt_pts(backlog_count)
        sp_field = get_board_story_points_field(board_id)
        no_story_pts_count = get_sprint_issues_no_story_points_count(sprint_id, sprint_name, story_points_field=sp_field)
        header = (
            "### **Sprint details**\n\n"
            "| **Sprint** | **Details** |\n"
            "|------------|-------------|\n"
            f"| Sprint | {sprint_name} |\n"
            f"| Actual Start | {sprint_info.get('startDate', '—')} |\n"
            f"| Actual End | {sprint_info.get('endDate', '—')} |\n"
            f"| Completed story points (Jira resolved) | {completed_display} |\n"
            f"| Issues in Sprint | {sprint_issue_count} |\n"
            f"| Issues in backlog | {backlog_display} |\n"
            f"| Issues with no story points | {no_story_pts_count} |\n"
            f"| Total Story Points | {total_pts_display} |\n"
        )
        if naming_warning:
            header += f"| ⚠️ Warning | {naming_warning} |\n"
        header += (
            f"| Date | {datetime.now().strftime('%B %d, %Y')} |\n"
            f"| Team Members | {', '.join(detected_members)} |\n\n"
        )

        last_closed_sprint_id = get_last_closed_sprint_id(board_id)
        if last_closed_sprint_id:
            spillover, try_jql_fallback = get_sprint_report_spillover(board_id, last_closed_sprint_id)
            if try_jql_fallback:
                spillover = get_spillover_via_jql(last_closed_sprint_id)
            if spillover:
                header += "### **Spillover from previous sprint**\n\n*Issues not completed in the last closed sprint (carried over):*\n\n"
                header += "| **Issue** | **Summary** | **Assignee** |\n|------------|-------------|----------|\n"
                for s in spillover:
                    key = s.get("key") or "—"
                    summary = escape_markdown_table_cell(s.get("summary") or "", 80)
                    assignee = escape_markdown_table_cell(str(s.get("assignee") or "—"), 40)
                    header += f"| [{key}]({JIRA_SERVER}/browse/{key}) | {summary} | {assignee} |\n"
                header += "\n"
            else:
                header += "### **Spillover from previous sprint**\n\n*No spillover issues from the last closed sprint.*\n\n"
        header += "---\n\n"
        full_report = header + report
        return full_report, None, naming_warning, verification_violations

    except Exception as e:
        error_msg = f"Error generating report for team {team_name}: {e}"
        logger.error(error_msg)
        return None, error_msg, "", []


def build_consolidated_report(all_team_reports: list[dict]) -> str:
    """Single Confluence page content from per-team reports (markdown + minimal inline styles)."""
    teams = ', '.join(t['team_name'] for t in all_team_reports) or 'None'
    date_str = datetime.now().strftime('%B %d, %Y')

    consolidated = (
        f'<p style="text-align: center;">Date: {date_str}</p>\n'
        f'<p style="text-align: center;">Teams Included: {teams}</p>\n\n'
        "---\n\n"
    )

    if APP_BASE_URL:
        person_url = f"{APP_BASE_URL}/person"
        velocity_url = (VELOCITY_DASHBOARD_URL or "").strip()
        consolidated += (
            '<div style="text-align: center; margin: 0 0 12px 0;">'
            f'<a href="{person_url}" target="_blank" rel="noopener" style="font-weight: bold; text-decoration: none;">🔍 Developer lookup</a>'
            '</div>'
            '<div style="text-align: center; margin: 0 0 12px 0;">'
            + (f'<a href="{velocity_url}" target="_blank" rel="noopener" style="font-weight: bold; text-decoration: none;">📊 Sprint metrics dashboard</a>' if velocity_url else '<strong>📊 Sprint metrics dashboard</strong>')
            + '</div>'
            f'<div style="margin: 12px 0;">'
            f'<iframe src="{person_url}" width="100%" height="700" '
            'style="border: 1px solid #dfe1e6; border-radius: 6px; min-height: 700px; background: #fff;" '
            'title="Developer lookup"></iframe>'
            "</div>\n\n"
        )

    for team_data in all_team_reports:
        team_name = team_data["team_name"]
        report = team_data["report"]
        w = team_data.get("naming_warning")
        violations = team_data.get("verification_violations") or []

        if "\n\n---\n\n" in report:
            header_part, ai_content = report.split("\n\n---\n\n", 1)
        else:
            header_part, ai_content = report, ""
        if "### Spillover from previous sprint" in header_part:
            title_and_table, spillover_rest = header_part.split("### Spillover from previous sprint", 1)
            spillover_block = "### Spillover from previous sprint" + spillover_rest
        else:
            title_and_table = header_part
            spillover_block = ""

        if w and "| ⚠️ Warning |" in title_and_table:
            title_and_table = re.sub(r"\|\s*⚠️ Warning\s*\|[^\n]+\|\n", "", title_and_table)
        consolidated += f"## **<u>Team: {team_name}</u>**\n\n" + title_and_table.strip() + "\n\n"
        if w:
            consolidated += "### **Naming Alerts**\n\n" + f"- {w}\n\n"
        if violations:
            counts = _aggregate_violation_counts(violations)
            consolidated += "### **Jira Verification Violations**\n"
            consolidated += _violations_summary_table(counts) + "\n"
            consolidated += "### **Per-issue details**\n\n| **Issue** | **Status** | **Assignee** | **Violations** |\n|-----------|------------|----------|----------------|\n"
            for v in violations:
                key = v.get("issue_key") or "—"
                status = escape_markdown_table_cell(str(v.get("status") or ""), 40)
                assignee = escape_markdown_table_cell(str(v.get("assignee") or "—"), 40)
                violations_text = " • ".join(v.get("violations") or [])
                violations_cell = escape_markdown_table_cell(violations_text, 200)
                consolidated += f"| [{key}]({JIRA_SERVER}/browse/{key}) | {status} | {assignee} | {violations_cell} |\n"
            consolidated += "\n"
        if spillover_block:
            consolidated += spillover_block.strip() + "\n\n"
        consolidated += "---\n\n" + ai_content + "\n\n" + "=" * 80 + "\n\n"

    return consolidated


CONFLUENCE_PAGE_TITLE = "HSTG Digital Jira status report"


def get_confluence_title() -> str:
    """Return the fixed Confluence page title (same page is overwritten each run)."""
    return CONFLUENCE_PAGE_TITLE


def build_teams_message(
    all_team_reports: list[dict],
    confluence_url: str | None,
    results: dict,
) -> str:
    """Build the message body to send to Teams (report link + date + teams + errors)."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    time_12h = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'} IST"
    date_str = f"{now.strftime('%B %d, %Y')} {time_12h}"
    teams_included = ", ".join(t["team_name"] for t in all_team_reports)
    link_line = f"🔗 [Open in Confluence]({confluence_url})\n\n" if confluence_url else "Confluence page could not be created (duplicate title or error). Please check Confluence or try again later.\n\n"
    msg = f"📊 **HSTG Digital Jira status report**\n\n**Date:** {date_str}\n\n{link_line}**Teams Included:** {teams_included}\n"
    error_entries = [f"- {name}: {data['message']}" for name, data in results.get("teams", {}).items() if data.get("status") == "error"]
    if error_entries:
        msg += "\n**Error:**\n" + "\n".join(error_entries)
    return msg
