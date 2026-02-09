"""Jira issue fetching, team members, and markdown helpers."""
import logging

from config import jira_client, JIRA_SERVER, JIRA_STORY_POINTS_FIELD
from team_config import JIRA_CUSTOM_JQL_FILTER

from jira_api.verification_helpers import check_verification_checklist

logger = logging.getLogger(__name__)


def escape_markdown_table_cell(text: str, max_length: int = 100) -> str:
    """Escape and clean text for markdown table cell."""
    if not text:
        return ""
    text = str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text[:max_length] + "..." if len(text) > max_length else text


def get_team_members_from_sprint(sprint_name: str, board_id: int) -> list[str]:
    """Auto-detect team members from active sprint (assignees of issues in the current sprint)."""
    try:
        jql = f'sprint = "{sprint_name}"'
        if JIRA_CUSTOM_JQL_FILTER:
            jql += f' AND {JIRA_CUSTOM_JQL_FILTER}'
        issues = jira_client.search_issues(jql, maxResults=500)
        team_members = {
            issue.fields.assignee.displayName
            for issue in issues
            if issue.fields.assignee and issue.fields.assignee.displayName
        }
        members_list = sorted(team_members)
        logger.info(f"Auto-detected {len(members_list)} team members from active sprint: {members_list}")
        return members_list
    except Exception as e:
        logger.error(f"Failed to auto-detect team members from sprint: {e}")
        return []


def _build_jql_for_team(sprint_name: str, team_config: dict) -> str:
    """Build JQL for team issues (sprint + optional members filter + custom filter)."""
    if team_config.get("members"):
        members_jql = " OR ".join([f'assignee = "{m}"' for m in team_config["members"]])
        jql = f'sprint = "{sprint_name}" AND ({members_jql})'
    else:
        jql = f'sprint = "{sprint_name}"'
    if JIRA_CUSTOM_JQL_FILTER:
        jql += f' AND {JIRA_CUSTOM_JQL_FILTER}'
    return jql


def _parse_story_point_value(raw) -> float:
    """Parse a story point value from API (number, string, or dict like {'value': 3})."""
    if raw is None:
        return 0.0
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("id")
    if raw is None:
        return 0.0
    try:
        v = float(raw)
        return v if v >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _get_story_points(issue, story_points_field: str | None = None) -> float:
    """Get story points from issue using the field from Jira (board config or JIRA_STORY_POINTS_FIELD). Reads issue.fields then issue.raw['fields']."""
    field = story_points_field or JIRA_STORY_POINTS_FIELD
    if not field:
        logger.debug("_get_story_points: no field configured (board or JIRA_STORY_POINTS_FIELD)")
        return 0.0

    raw = getattr(issue.fields, field, None)
    if raw is not None:
        v = _parse_story_point_value(raw)
        if v >= 0:
            logger.debug("_get_story_points: %s -> %s (from issue.fields.%s)", issue.key, v, field)
            return v

    raw_top = getattr(issue, "raw", None) or {}
    fields_dict = raw_top.get("fields") if isinstance(raw_top, dict) else {}
    if isinstance(fields_dict, dict):
        raw = fields_dict.get(field)
        if raw is not None:
            v = _parse_story_point_value(raw)
            logger.debug("_get_story_points: %s -> %s (from issue.raw['fields'].%s)", issue.key, v, field)
            return v

    logger.debug("_get_story_points: %s -> 0 (field=%s not in issue.fields or raw['fields'])", issue.key, field)
    return 0.0


def fetch_issues_for_team(team_name: str, team_config: dict, sprint_name: str) -> tuple[str, int, list, int | float]:
    """Fetch Jira issues for the team; return (context_markdown, issue_count, verification_violations, total_story_points)."""
    jql = _build_jql_for_team(sprint_name, team_config)
    logger.info(f"Fetching issues for team {team_name} with JQL: {jql}")
    fields = ["summary", "status", "assignee", "reporter"]
    if JIRA_STORY_POINTS_FIELD:
        fields.append(JIRA_STORY_POINTS_FIELD)
    try:
        issues = jira_client.search_issues(jql, maxResults=100, fields=",".join(fields))
        logger.info(f"Found {len(issues)} issues for team {team_name}")
        lines = []
        verification_violations = []
        total_story_points = 0
        for issue in issues:
            try:
                verification_result = check_verification_checklist(jira_client, issue)
                if not verification_result["is_compliant"]:
                    verification_violations.append(verification_result)
                total_story_points += _get_story_points(issue)
                icon = "✅" if verification_result["is_compliant"] else "⚠️"
                summary = (issue.fields.summary or "")[:80].replace("\n", " ")
                status = issue.fields.status.name if issue.fields.status else "—"
                assignee = issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned"
                lines.append(f"- {icon} [{issue.key}]({JIRA_SERVER}/browse/{issue.key}): {summary} | Status: {status} | Assignee: {assignee}")
            except Exception as e:
                logger.error(f"Error processing issue {issue.key}: {e}")
        return "\n".join(lines), len(issues), verification_violations, total_story_points
    except Exception as e:
        logger.error(f"Failed to fetch issues for team {team_name}: {e}")
        return "", 0, [], 0
