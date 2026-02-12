"""Jira issue fetching, team members, and markdown helpers."""
import re
import logging

from config import jira_client, JIRA_SERVER, JIRA_STORY_POINTS_FIELD
from team_config import JIRA_CUSTOM_JQL_FILTER, JIRA_REPORT_ISSUE_TYPES_JQL
from utils.markdown import escape_markdown_table_cell

from jira_api.verification_helpers import check_verification_checklist

logger = logging.getLogger(__name__)


def _strip_html(text, max_len: int = 400) -> str:
    """Strip HTML tags and normalize whitespace; truncate to max_len. Handles ADF dict or string."""
    if text is None:
        return ""
    if isinstance(text, dict):
        text = text.get("plain", "") or text.get("content", "") or str(text)
    s = re.sub(r"<[^>]+>", " ", str(text))
    s = " ".join(s.split())
    return s[:max_len] + "..." if len(s) > max_len else s


def _is_blocked_status(status_id: str | None, status_name: str, status_to_bucket: dict[str, str]) -> bool:
    """True if this status is Blocked/On Hold (board column config or status name fallback)."""
    if status_to_bucket.get(str(status_id)) == "blocked":
        return True
    name = (status_name or "").lower().strip()
    return name in ("blocked", "on hold", "onhold", "waiting") or "blocked" in name or "on hold" in name


def get_blocked_issues_with_context(
    sprint_id: int | None,
    sprint_name: str | None,
    status_to_bucket: dict[str, str],
) -> list[dict]:
    """Issues in Blocked/On Hold for this sprint with comment context. Uses status_to_bucket and status name fallback."""
    out = []
    try:
        if sprint_id:
            jql = f"sprint = {sprint_id}"
        elif sprint_name:
            jql = f'sprint = "{sprint_name}"'
        else:
            return out
        if JIRA_CUSTOM_JQL_FILTER:
            jql += f" AND {JIRA_CUSTOM_JQL_FILTER}"
        issues = jira_client.search_issues(jql, maxResults=200, fields=["summary", "status"])
        for issue in issues:
            status_obj = issue.fields.status
            status_name = status_obj.name if status_obj else "—"
            status_id = getattr(status_obj, "id", None)
            if status_id is None and status_obj:
                raw = (getattr(issue, "raw", None) or {}).get("fields", {}).get("status")
                status_id = raw.get("id") if isinstance(raw, dict) else None
            if not _is_blocked_status(status_id, status_name, status_to_bucket):
                continue
            try:
                comments = jira_client.comments(issue.key)
                bodies = [_strip_html(getattr(c, "body", None) or "", 200) for c in list(comments)[-3:]]
                comments_snippet = " | ".join(b for b in bodies if b).strip() or "No comments."
            except Exception as e:
                logger.debug("Comments for %s: %s", issue.key, e)
                comments_snippet = "Comments not available."
            out.append({
                "key": issue.key,
                "summary": (issue.fields.summary or "")[:100],
                "status_name": status_name,
                "comments_snippet": comments_snippet,
            })
    except Exception as e:
        logger.warning("get_blocked_issues_with_context: %s", e)
    return out


def get_sprint_issues_by_status_with_context(
    sprint_id: int | None,
    sprint_name: str | None,
    status: str,
) -> list[dict]:
    """Issues in sprint with given status (e.g. BACKLOG, ON HOLD), with comment context. Parent issues only."""
    out = []
    try:
        if sprint_id:
            jql = f"sprint = {sprint_id} AND status = \"{status}\" AND {JIRA_REPORT_ISSUE_TYPES_JQL}"
        elif sprint_name:
            jql = f'sprint = "{sprint_name}" AND status = "{status}" AND {JIRA_REPORT_ISSUE_TYPES_JQL}'
        else:
            return out
        if JIRA_CUSTOM_JQL_FILTER:
            jql += f" AND {JIRA_CUSTOM_JQL_FILTER}"
        issues = jira_client.search_issues(jql, maxResults=200, fields=["summary", "status"])
        for issue in issues:
            status_obj = issue.fields.status
            status_name = status_obj.name if status_obj else "—"
            try:
                comments = jira_client.comments(issue.key)
                bodies = [_strip_html(getattr(c, "body", None) or "", 200) for c in list(comments)[-3:]]
                comments_snippet = " | ".join(b for b in bodies if b).strip() or "No comments."
            except Exception as e:
                logger.debug("Comments for %s: %s", issue.key, e)
                comments_snippet = "Comments not available."
            out.append({
                "key": issue.key,
                "summary": (issue.fields.summary or "")[:100],
                "status_name": status_name,
                "comments_snippet": comments_snippet,
            })
    except Exception as e:
        logger.warning("get_sprint_issues_by_status_with_context: %s", e)
    return out


def get_team_members_from_sprint(sprint_name: str, board_id: int, sprint_id: int | None = None) -> list[str]:
    """Auto-detect team members from active sprint (assignees of issues in the current sprint). Use sprint_id when available so the same name in different years matches only one sprint."""
    try:
        if sprint_id is not None:
            jql = f"sprint = {sprint_id}"
        else:
            jql = f'sprint = "{sprint_name}"'
        if JIRA_CUSTOM_JQL_FILTER:
            jql += f' AND {JIRA_CUSTOM_JQL_FILTER}'
        issues = jira_client.search_issues(jql, maxResults=500)
        team_members = {
            issue.fields.assignee.displayName
            for issue in issues
            if issue.fields.assignee and issue.fields.assignee.displayName
        }
        return sorted(team_members)
    except Exception as e:
        logger.error(f"Failed to auto-detect team members from sprint: {e}")
        return []


def _build_jql_for_team(sprint_name: str, team_config: dict, sprint_id: int | None = None) -> str:
    """Build JQL for team issues (sprint + optional members filter + custom filter). Use sprint_id when available so the same name in different years (e.g. PI2 S3 Dig Exp 2025 vs 2026) matches only one sprint."""
    if sprint_id is not None:
        sprint_clause = f"sprint = {sprint_id}"
    else:
        sprint_clause = f'sprint = "{sprint_name}"'
    if team_config.get("members"):
        members_jql = " OR ".join([f'assignee = "{m}"' for m in team_config["members"]])
        jql = f"{sprint_clause} AND ({members_jql})"
    else:
        jql = sprint_clause
    if JIRA_CUSTOM_JQL_FILTER:
        jql += f' AND {JIRA_CUSTOM_JQL_FILTER}'
    return jql


def get_sprint_issue_count(
    sprint_id: int | None, sprint_name: str | None, *, use_custom_filter: bool = False
) -> int:
    """Total issues in the sprint. use_custom_filter=False: parent issues only (matches Jira board total, includes resolved)."""
    if sprint_id:
        jql = f"sprint = {sprint_id}"
    elif sprint_name:
        jql = f'sprint = "{sprint_name}"'
    else:
        return 0
    if use_custom_filter and JIRA_CUSTOM_JQL_FILTER:
        jql += f" AND {JIRA_CUSTOM_JQL_FILTER}"
    else:
        jql += f" AND {JIRA_REPORT_ISSUE_TYPES_JQL}"
    try:
        issues = jira_client.search_issues(jql, maxResults=1000, fields=["key"])
        return len(issues)
    except Exception as e:
        logger.warning("get_sprint_issue_count: %s", e)
        return 0


def get_sprint_backlog_status_count(sprint_id: int | None, sprint_name: str | None) -> int:
    """Count issues in the sprint with status BACKLOG (not yet started). Matches Jira view 'sprint = X AND status = BACKLOG'."""
    if sprint_id:
        jql = f"sprint = {sprint_id} AND status = \"BACKLOG\" AND {JIRA_REPORT_ISSUE_TYPES_JQL}"
    elif sprint_name:
        jql = f'sprint = "{sprint_name}" AND status = "BACKLOG" AND {JIRA_REPORT_ISSUE_TYPES_JQL}'
    else:
        return 0
    try:
        issues = jira_client.search_issues(jql, maxResults=1000, fields=["key"])
        return len(issues)
    except Exception as e:
        logger.warning("get_sprint_backlog_status_count: %s", e)
        return 0


def get_sprint_issues_no_story_points_count(
    sprint_id: int | None,
    sprint_name: str | None,
    story_points_field: str | None = None,
) -> int:
    """Count issues in the sprint (parent only) that have no story points (0 or empty)."""
    if sprint_id:
        jql = f"sprint = {sprint_id} AND {JIRA_REPORT_ISSUE_TYPES_JQL}"
    elif sprint_name:
        jql = f'sprint = "{sprint_name}" AND {JIRA_REPORT_ISSUE_TYPES_JQL}'
    else:
        return 0
    field = story_points_field or JIRA_STORY_POINTS_FIELD
    if not field:
        return 0
    try:
        issues = jira_client.search_issues(jql, maxResults=1000, fields=[field])
        return sum(1 for i in issues if _get_story_points(i, story_points_field=field) == 0)
    except Exception as e:
        logger.warning("get_sprint_issues_no_story_points_count: %s", e)
        return 0


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
    """Story points from issue (board config or JIRA_STORY_POINTS_FIELD)."""
    field = story_points_field or JIRA_STORY_POINTS_FIELD
    if not field:
        return 0.0
    raw = getattr(issue.fields, field, None)
    if raw is not None:
        v = _parse_story_point_value(raw)
        if v >= 0:
            return v
    raw_top = getattr(issue, "raw", None) or {}
    fields_dict = raw_top.get("fields") if isinstance(raw_top, dict) else {}
    if isinstance(fields_dict, dict):
        raw = fields_dict.get(field)
        if raw is not None:
            v = _parse_story_point_value(raw)
            if v >= 0:
                return v
    return 0.0


def fetch_issues_for_team(
    team_name: str, team_config: dict, sprint_name: str, sprint_id: int | None = None
) -> tuple[str, int, list, int | float]:
    """Fetch Jira issues for the team; return (context_markdown, issue_count, verification_violations, total_story_points). Use sprint_id when available so the same sprint name in different years (e.g. 2025 vs 2026) matches only one sprint."""
    jql = _build_jql_for_team(sprint_name, team_config, sprint_id=sprint_id)
    fields = ["summary", "status", "assignee", "reporter", "issuelinks", "remotelinks"]
    if JIRA_STORY_POINTS_FIELD:
        fields.append(JIRA_STORY_POINTS_FIELD)
    try:
        issues = jira_client.search_issues(jql, maxResults=100, fields=",".join(fields))
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
