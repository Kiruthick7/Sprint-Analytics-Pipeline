"""Build team reports and consolidated Confluence/Teams content."""
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import JIRA_SERVER, APP_BASE_URL, VELOCITY_DASHBOARD_URL
from jira_api.jira_sprint import (
    derive_previous_sprint_name,
    get_active_sprint_info,
    get_allowed_sprint_info,
    get_closed_sprint_info_by_name_and_year,
    get_last_closed_sprint_info,
    get_last_closed_sprint_info_allowed,
    get_sprint_report_spillover,
    get_sprint_story_points_from_issues,
    get_sprint_total_story_points,
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
)
from report.llm_report import sprint_chain_comments
from report.violations_chart import aggregate_violation_counts, violations_summary_table
from utils.markdown import escape_markdown_table_cell

logger = logging.getLogger(__name__)


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


def generate_team_report(team_name: str, team_config: dict) -> tuple[str | None, str | None, str, list]:
    """Generate report for a specific team. Returns (report_markdown, error_message, naming_warning, verification_violations)."""
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
                return None, "⏭️ No allowed sprint for this team (excluded sprints only).", "", []

        if not sprint_info:
            logger.warning(f"No active sprint found for team {team_name}")
            return None, "⚠️ No active sprint found for team {team_name}", "", []

        sprint_name = sprint_info.get("name") or "—"

        sprint_name_valid, sprint_name_message = validate_sprint_name_format(sprint_name)
        naming_warning = ""
        if not sprint_name_valid:
            naming_warning = f"⚠️ Sprint name '{sprint_name}' does not match PI<X> S<X> Squad_Name. {sprint_name_message}"

        sprint_id = sprint_info.get("id")
        issues_markdown, issue_count, verification_violations, total_story_points = fetch_issues_for_team(team_name, team_config, sprint_name, sprint_id=sprint_id)
        if not issues_markdown:
            return None, f"ℹ️ No issues found for team {team_name} in sprint {sprint_name}", "", []

        backlog_count = get_sprint_backlog_status_count(sprint_id, sprint_name)
        backlog_list = get_sprint_issues_by_status_with_context(sprint_id, sprint_name, "BACKLOG")
        on_hold_list = get_sprint_issues_by_status_with_context(sprint_id, sprint_name, "ON HOLD")

        def _issues_context_with_comments(items: list, empty_msg: str) -> str:
            if not items:
                return empty_msg
            return "\n".join(
                f"- {b['key']} - {b['status_name']} - {b['summary']} | Comments/excerpts: {b['comments_snippet']}"
                for b in items
            )

        def _parse_comment_summaries(text: str) -> dict[str, str]:
            """Parse LLM output into key -> one-line summary. Expects lines like 'KEY: summary'."""
            out = {}
            for line in (text or "").strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                # Match "KEY: rest" (KEY = project id + hyphen + number, e.g. DGROCERY-7216)
                m = re.match(r"^([A-Z][A-Z0-9]+-\d+):\s*(.*)$", line, re.IGNORECASE)
                if m:
                    out[m.group(1).upper()] = m.group(2).strip()
            return out

        # LLM: one-line comment summary per issue (parsed and merged into each line)
        backlog_issues_context = _issues_context_with_comments(backlog_list, "(No issues on Backlog in this sprint.)")
        on_hold_issues_context = _issues_context_with_comments(on_hold_list, "(No issues on Hold in this sprint.)")
        comment_result = sprint_chain_comments.invoke({
            "team_name": team_name,
            "sprint_name": sprint_name,
            "backlog_issues_context": backlog_issues_context,
            "on_hold_issues_context": on_hold_issues_context,
        })
        comment_text = comment_result.content if hasattr(comment_result, "content") else str(comment_result)
        comment_by_key = _parse_comment_summaries(comment_text)

        # Format: numbered list (1. 2. 3.) so each issue is one line; sno. ticket number - status - ticket name - summary
        def _format_issue_line(item: dict, sno: int) -> str:
            key_upper = (item["key"] or "").upper()
            summary_of_comments = comment_by_key.get(key_upper, "No comments.")
            ticket_name = (item.get("summary") or "").strip()
            return f"{sno}. {item['key']} - {item['status_name']} - {ticket_name} - {summary_of_comments}"

        blockers_lines = ["### **Blockers, Delays or Issues Needing Attention**\n"]
        blockers_lines.append("**Issues on Backlog:**")
        if backlog_list:
            for i, b in enumerate(backlog_list, start=1):
                blockers_lines.append(_format_issue_line(b, i))
                blockers_lines.append("")  # blank line so each item is on its own line
        else:
            blockers_lines.append("None.")
        blockers_lines.append("")
        blockers_lines.append("**Issues on Hold:**")
        if on_hold_list:
            for i, b in enumerate(on_hold_list, start=1):
                blockers_lines.append(_format_issue_line(b, i))
                blockers_lines.append("")  # blank line so each item is on its own line
        else:
            blockers_lines.append("None.")

        report = "\n".join(blockers_lines)
        report = re.sub(r"\n{3,}", "\n\n", report)

        detected_members = team_config.get("members") or get_team_members_from_sprint(sprint_name, board_id, sprint_id=sprint_id)

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

        excluded = team_config.get("excluded_sprint_names") or []
        included = team_config.get("included_sprint_names")
        current_year = datetime.now().year
        last_closed_sprint = None
        if excluded or included:
            previous_sprint_name = derive_previous_sprint_name(sprint_name)
            if previous_sprint_name:
                last_closed_sprint = get_closed_sprint_info_by_name_and_year(board_id, previous_sprint_name, current_year)
            if not last_closed_sprint:
                last_closed_sprint = get_last_closed_sprint_info_allowed(board_id, excluded, included, year=current_year)
        else:
            last_closed_sprint = get_last_closed_sprint_info(board_id, year=current_year)
        spillover = []
        if last_closed_sprint:
            last_closed_sprint_id = last_closed_sprint["id"]
            spillover, try_jql_fallback = get_sprint_report_spillover(board_id, last_closed_sprint_id)
            if try_jql_fallback:
                spillover = get_spillover_via_jql(last_closed_sprint_id)
        prev_sprint_name = (last_closed_sprint or {}).get("name") or ""
        prev_sprint_date = (last_closed_sprint or {}).get("endDate") or ""
        if spillover:
            header += "### **Spillover from previous sprint**\n\n"
            name_label = prev_sprint_name or "last closed sprint"
            date_label = prev_sprint_date or "—"
            header += f"*Issues not completed in the last closed sprint ({name_label}, closed {date_label}) (carried over):*\n\n"
            header += "| **Issue** | **Summary** | **Assignee** | **Label** |\n|------------|-------------|----------|------|\n"
            for s in spillover:
                key = s.get("key") or "—"
                summary = escape_markdown_table_cell(s.get("summary") or "", 80)
                assignee = escape_markdown_table_cell(str(s.get("assignee") or "—"), 40)
                label = escape_markdown_table_cell(str(s.get("label") or ""), 20)
                header += f"| [{key}]({JIRA_SERVER}/browse/{key}) | {summary} | {assignee} | {label} |\n"
            header += "\n"
        else:
            prev_label = ""
            if prev_sprint_name or prev_sprint_date:
                prev_label = f" ({prev_sprint_name}, closed {prev_sprint_date})" if prev_sprint_name and prev_sprint_date else f" ({prev_sprint_name})" if prev_sprint_name else f" (closed {prev_sprint_date})"
            header += f"### **Spillover from previous sprint**\n\n*No spillover issues from the last closed sprint{prev_label}.*\n\n"
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
            '<div style="width: 100%; text-align: center; margin: 16px 0;">'
            '<p style="margin: 8px 0;"><a href="' + person_url + '" target="_blank" rel="noopener" style="font-weight: bold; text-decoration: none;">🔍 Developer lookup</a></p>'
            '<p style="margin: 8px 0;">' + (f'<a href="{velocity_url}" target="_blank" rel="noopener" style="font-weight: bold; text-decoration: none;">📊 Sprint metrics dashboard</a>' if velocity_url else '<strong>📊 Sprint metrics dashboard</strong>') + '</p>'
            '</div>'
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
            counts = aggregate_violation_counts(violations)
            consolidated += "### **Jira Verification Violations**\n"
            consolidated += violations_summary_table(counts) + "\n"
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


CONFLUENCE_PAGE_TITLE = "HSTG Digital Sprint status report"


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
    msg = f"📊 **HSTG Digital Sprint status report**\n\n**Date:** {date_str}\n\n{link_line}**Teams Included:** {teams_included}\n"
    error_entries = [f"- {name}: {data['message']}" for name, data in results.get("teams", {}).items() if data.get("status") == "error"]
    if error_entries:
        msg += "\n**Error:**\n" + "\n".join(error_entries)
    return msg
