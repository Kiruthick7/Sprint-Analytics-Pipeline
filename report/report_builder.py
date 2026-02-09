"""Build team reports and consolidated Confluence/Teams content."""
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import logging

from config import JIRA_SERVER, APP_BASE_URL
from jira_api.jira_sprint import (
    get_active_sprint_info,
    get_allowed_sprint_info,
    get_last_closed_sprint_id,
    get_sprint_report_spillover,
    get_sprint_total_story_points,
    get_spillover_via_jql,
    get_velocity_history,
    validate_sprint_name_format,
)
from jira_api.jira_issues import (
    fetch_issues_for_team,
    get_team_members_from_sprint,
    escape_markdown_table_cell,
)

from report.llm_report import sprint_chain

logger = logging.getLogger(__name__)


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

        logger.info(f"Generating AI summary for team {team_name} with {issue_count} issues")
        result = sprint_chain.invoke({
            "team_name": team_name,
            "sprint_name": sprint_name,
            "sprint_features": issues_markdown,
        })
        report = result.content if hasattr(result, "content") else str(result)

        if team_config.get("members"):
            detected_members = team_config["members"]
        else:
            detected_members = get_team_members_from_sprint(sprint_name, team_config["board_id"])

        sprint_id = sprint_info.get("id")
        team_velocity_count = len(team_config["members"]) if team_config.get("members") else len(detected_members)
        sprint_velocity_count = len(detected_members)
        total_pts_from_sprint = (
            get_sprint_total_story_points(
                board_id, sprint_id, sprint_name=sprint_name, use_report_jql=True
            )
            if sprint_id is not None
            else None
        )
        if total_pts_from_sprint is not None:
            total_pts_display = total_pts_from_sprint
        else:
            total_pts_display = int(total_story_points) if isinstance(total_story_points, (int, float)) and total_story_points == int(total_story_points) else total_story_points
            if total_pts_display == 0:
                total_pts_display = "—"
        header = (
            f"# 📊 Daily Jira Status Report - {team_name}\n\n"
            "| Sprint | Details |\n"
            "|--------|---------|\n"
            f"| **Sprint** | {sprint_name} |\n"
            f"| **Actual Start** | {sprint_info.get('startDate', '—')} |\n"
            f"| **Actual End** | {sprint_info.get('endDate', '—')} |\n"
            f"| **Sprint Velocity** | {sprint_velocity_count} |\n"
            f"| **Team Velocity** | {team_velocity_count} |\n"
            f"| **Issues in Sprint** | {issue_count} |\n"
            f"| **Total Story Points** | {total_pts_display} |\n"
        )
        if naming_warning:
            header += f"| **⚠️ Warning** | {naming_warning} |\n"
        header += (
            f"| **Date** | {datetime.now().strftime('%B %d, %Y')} |\n"
            f"| **Team Members** | {', '.join(detected_members)} |\n\n"
        )

        velocity_rows = get_velocity_history(board_id, num_sprints=5)
        if velocity_rows:
            header += "### Velocity (story points)\n\n"
            header += "| Sprint | Commitment | Completed |\n|--------|------------|----------|\n"
            completed_nums = []
            for row in velocity_rows:
                name = escape_markdown_table_cell(row.get("sprint_name") or "—", 50)
                header += f"| {name} | {row.get('commitment', '—')} | {row.get('completed', '—')} |\n"
                if row.get("completed_num") is not None:
                    completed_nums.append(row["completed_num"])
            completed_avg = round(sum(completed_nums) / len(completed_nums), 1) if completed_nums else None
            if completed_avg is not None:
                header += f"| **Average** | — | **{completed_avg}** |\n"
            header += "\n"

        last_closed_sprint_id = get_last_closed_sprint_id(board_id)
        if last_closed_sprint_id:
            spillover, try_jql_fallback = get_sprint_report_spillover(board_id, last_closed_sprint_id)
            if try_jql_fallback:
                spillover = get_spillover_via_jql(last_closed_sprint_id)
            if spillover:
                header += "### Spillover from previous sprint\n\nIssues not completed in the last closed sprint (carried over):\n\n"
                header += "| Issue | Summary |\n|-------|--------|\n"
                for s in spillover:
                    key = s.get("key") or "—"
                    summary = escape_markdown_table_cell(s.get("summary") or "", 80)
                    header += f"| [{key}]({JIRA_SERVER}/browse/{key}) | {summary} |\n"
                header += "\n"
            else:
                header += "### Spillover from previous sprint\n\nNo spillover issues from the last closed sprint.\n\n"
        header += "---\n\n"
        full_report = header + report
        return full_report, None, naming_warning, verification_violations

    except Exception as e:
        error_msg = f"Error generating report for team {team_name}: {e}"
        logger.error(error_msg)
        return None, error_msg, "", []


def build_consolidated_report(all_team_reports: list[dict]) -> str:
    """Build single Confluence page content from per-team reports."""
    consolidated = (
        "# 📊 Daily Jira Status Report - All Teams\n\n"
        f"**Date:** {datetime.now().strftime('%B %d, %Y')}\n"
        f"**Teams Included:** {', '.join(t['team_name'] for t in all_team_reports)}\n\n"
        "---\n\n"
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

        if w and "| **⚠️ Warning** |" in title_and_table:
            title_and_table = re.sub(r"\|\s*\*\*⚠️ Warning\*\*\s*\|[^\n]+\|\n", "", title_and_table)
        consolidated += f"## Team: {team_name}\n\n" + title_and_table.strip() + "\n\n"
        if w:
            consolidated += "**Naming Alerts:**\n\n" + f"- {w}\n\n"
        if violations:
            consolidated += "**Jira Verification Violations:**\n\n| Issue | Status | Violations |\n|-------|--------|------------|\n"
            for v in violations:
                key = v.get("issue_key") or "—"
                status = escape_markdown_table_cell(str(v.get("status") or ""), 40)
                violations_text = " • ".join(v.get("violations") or [])
                violations_cell = escape_markdown_table_cell(violations_text, 200)
                consolidated += f"| [{key}]({JIRA_SERVER}/browse/{key}) | {status} | {violations_cell} |\n"
            consolidated += "\n"
        if spillover_block:
            consolidated += spillover_block.strip() + "\n\n"
        consolidated += "---\n\n" + ai_content + "\n\n" + "=" * 80 + "\n\n"

    if APP_BASE_URL:
        consolidated += (
            "---\n\n"
            "## 🔍 Look up a person\n\n"
            "**Open Person Lookup:** [**Open Person Lookup in a new tab**]({url}/person) — type a developer name, select team/sprint, and click **Fetch from Jira** to see their story points and issues.\n\n"
            "*(Confluence may not show the embedded box below; use the link above if the box is empty.)*\n\n"
            '<div class="person-lookup-embed" style="margin: 16px 0;">'
            '<iframe src="{url}/person" width="100%" height="700" '
            'style="border: 1px solid #ddd; border-radius: 6px; min-height: 700px; background: #fff;" '
            'title="Person Lookup – if empty, use the link above"></iframe>'
            "</div>\n\n"
        ).format(url=APP_BASE_URL)
    return consolidated


# Fixed title so the same Confluence page is updated each run (no new page per day).
CONFLUENCE_PAGE_TITLE = "Daily Jira Status Report - All Teams"


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
    if confluence_url:
        msg = (
            f"📊 **Daily Jira Status Report - All Teams**\n\n"
            f"**Date:** {date_str}\n\n"
            f"🔗 [Open in Confluence]({confluence_url})\n\n"
            f"**Teams Included:** {teams_included}\n"
        )
    else:
        msg = (
            f"📊 **Daily Jira Status Report - All Teams**\n\n"
            f"**Date:** {date_str}\n\n"
            "Confluence page could not be created (duplicate title or error). Please check Confluence or try again later.\n\n"
            f"**Teams Included:** {teams_included}\n"
        )
    error_entries = [
        f"- {name}: {data['message']}"
        for name, data in results.get("teams", {}).items()
        if data.get("status") == "error"
    ]
    if error_entries:
        msg += "\n**Error:**\n" + "\n".join(error_entries)
    return msg
