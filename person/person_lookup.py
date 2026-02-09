"""Individual person lookup: list developers, sprints per board, and person's Jira details."""
import logging
from config import jira_client, JIRA_SERVER, JIRA_STORY_POINTS_FIELD
from team_config import TEAMS_CONFIG, JIRA_PERSON_LOOKUP_JQL_FILTER
from jira_api.jira_issues import _get_story_points
from jira_api.jira_sprint import (
    get_board_status_to_bucket,
    get_board_story_points_field,
    get_sprint_report_points_breakdown,
)

logger = logging.getLogger(__name__)

# Map Jira status names to not_started / in_progress / completed / blocked (lowercase for comparison)
_STATUS_COMPLETED = {"done", "closed", "resolved", "complete", "completed"}
_STATUS_BLOCKED = {"blocked", "on hold", "onhold", "waiting", "on hold (blocked)"}
_STATUS_IN_PROGRESS = {
    "in progress", "in development", "in review", "in test", "development", "review", "testing",
    "ready for dev", "ready for review",
}


def _status_bucket(status_name: str) -> str:
    """Return 'completed' | 'blocked' | 'in_progress' | 'not_started' (fallback when board column config not used)."""
    if not status_name:
        return "not_started"
    s = status_name.lower().strip()
    if s in _STATUS_COMPLETED:
        return "completed"
    if s in _STATUS_BLOCKED or "blocked" in s or "on hold" in s:
        return "blocked"
    if s in _STATUS_IN_PROGRESS or "progress" in s or "review" in s or "development" in s or "test" in s:
        return "in_progress"
    return "not_started"


def _bucket_for_status(
    status_id: str | None,
    status_name: str,
    status_to_bucket: dict[str, str],
) -> str:
    """Use board column mapping if available; always treat name-based 'completed'/'blocked' so they aren't lost."""
    by_name = _status_bucket(status_name)
    if by_name == "completed":
        return "completed"
    if by_name == "blocked":
        return "blocked"
    if status_to_bucket and status_id is not None:
        for key in (str(status_id).strip(), str(int(status_id)) if isinstance(status_id, (int, float)) else None):
            if key and key in status_to_bucket:
                return status_to_bucket[key]
    return by_name


def _resolve_sprint_id(board_id: int, sprint_name: str | None) -> int | None:
    """Resolve sprint id from board + sprint name (for Sprint Report API)."""
    if not sprint_name:
        return None
    try:
        for state in ("active", "future", "closed"):
            for s in jira_client.sprints(board_id, state=state):
                if getattr(s, "name", None) == sprint_name:
                    sid = getattr(s, "id", None)
                    return int(sid) if sid is not None else None
    except Exception as e:
        logger.debug("_resolve_sprint_id: %s", e)
    return None


def _sprint_breakdown_from_issues(
    board_id: int,
    sprint_id: int | None,
    sprint_name: str | None,
    sp_field: str | None,
) -> dict[str, float]:
    """Compute sprint story points by summing issues in the sprint (fallback when report fails or returns 0). Uses board column config for status buckets."""
    out: dict[str, float] = {"total": 0.0, "not_started": 0.0, "in_progress": 0.0, "completed": 0.0, "blocked": 0.0}
    if sprint_id:
        jql = f"sprint = {sprint_id}"
    elif sprint_name:
        jql = f'sprint = "{sprint_name}"'
    else:
        return out
    if JIRA_PERSON_LOOKUP_JQL_FILTER:
        jql += f" AND {JIRA_PERSON_LOOKUP_JQL_FILTER}"
    fields = ["status"]
    if sp_field:
        fields.append(sp_field)
    status_to_bucket = get_board_status_to_bucket(board_id)
    try:
        issues = jira_client.search_issues(jql, maxResults=500, fields=",".join(fields))
        logger.debug("_sprint_breakdown_from_issues: jql=%s sp_field=%s issues=%s", jql, sp_field, len(issues))
        for i in issues:
            status_obj = i.fields.status
            status_name = status_obj.name if status_obj else ""
            status_id = getattr(status_obj, "id", None) if status_obj else None
            if status_id is None and status_obj is not None:
                raw_status = (getattr(i, "raw", None) or {}).get("fields", {}).get("status")
                status_id = raw_status.get("id") if isinstance(raw_status, dict) else None
            bucket = _bucket_for_status(status_id, status_name, status_to_bucket)
            pts = _get_story_points(i, story_points_field=sp_field)
            out["total"] += pts
            out[bucket] += pts
    except Exception as e:
        logger.warning("_sprint_breakdown_from_issues: %s", e)
    return out


def get_sprint_story_points_breakdown(board_id: int, sprint_id: int | None = None, sprint_name: str | None = None) -> dict:
    """
    Get sprint-level story points: try Jira Sprint Report first; fall back to summing issues in sprint.
    Returns { total, not_started, in_progress, completed, blocked }.
    """
    out: dict[str, float] = {"total": 0.0, "not_started": 0.0, "in_progress": 0.0, "completed": 0.0, "blocked": 0.0}
    resolved_id = sprint_id or _resolve_sprint_id(board_id, sprint_name)
    sp_field = get_board_story_points_field(board_id) or JIRA_STORY_POINTS_FIELD

    issue_based = _sprint_breakdown_from_issues(board_id, resolved_id, sprint_name, sp_field)
    out["not_started"] = issue_based["not_started"]
    out["in_progress"] = issue_based["in_progress"]
    out["completed"] = issue_based["completed"]
    out["blocked"] = issue_based["blocked"]

    if resolved_id is not None:
        try:
            report = get_sprint_report_points_breakdown(board_id, resolved_id)
            if report is not None and report["total"] > 0:
                out["total"] = report["total"]
                logger.info("get_sprint_story_points_breakdown: total from report=%s; buckets not_started=%s in_progress=%s completed=%s blocked=%s", out["total"], out["not_started"], out["in_progress"], out["completed"], out["blocked"])
                return out
        except Exception as e:
            logger.warning("get_sprint_story_points_breakdown (sprint report): %s", e)

    out["total"] = issue_based["total"]
    logger.info("get_sprint_story_points_breakdown: issue-based only total=%s not_started=%s in_progress=%s completed=%s blocked=%s", out["total"], out["not_started"], out["in_progress"], out["completed"], out["blocked"])
    return out


def get_all_developers() -> list[dict]:
    """Fetch all developers across teams (from each team's active sprint assignees). Returns list of {name, team_name, board_id}."""
    seen = set()
    result = []
    for team_name, team_config in TEAMS_CONFIG.items():
        board_id = team_config.get("board_id")
        if not board_id:
            continue
        try:
            sprints = jira_client.sprints(board_id, state="active")
            if not sprints:
                sprints = jira_client.sprints(board_id, state="future")
            sprint_name = getattr(sprints[0], "name", None) if sprints else None
            if not sprint_name:
                continue
            jql = f'sprint = "{sprint_name}"'
            if JIRA_PERSON_LOOKUP_JQL_FILTER:
                jql += f" AND {JIRA_PERSON_LOOKUP_JQL_FILTER}"
            issues = jira_client.search_issues(jql, maxResults=500, fields=["assignee"])
            for issue in issues:
                if not issue.fields.assignee:
                    continue
                name = issue.fields.assignee.displayName
                key = (name, team_name, board_id)
                if key in seen:
                    continue
                seen.add(key)
                result.append({"name": name, "team_name": team_name, "board_id": board_id})
        except Exception as e:
            logger.warning(f"get_all_developers: team {team_name} board {board_id}: {e}")
    result.sort(key=lambda x: (x["name"].lower(), x["team_name"]))
    return result


def get_sprints_for_board(board_id: int) -> list[dict]:
    """Get sprints for a board (active first, then future, then recent closed). Returns list of {id, name, state}."""
    out = []
    try:
        for state in ("active", "future", "closed"):
            sprints = jira_client.sprints(board_id, state=state)
            for s in (sprints[:20] if state == "closed" else sprints):
                sid = getattr(s, "id", None)
                name = getattr(s, "name", None) or str(s)
                if sid is not None:
                    out.append({"id": int(sid), "name": name, "state": state})
    except Exception as e:
        logger.warning(f"get_sprints_for_board board_id={board_id}: {e}")
    return out


def get_person_details(developer_name: str, board_id: int, sprint_id: int | None = None, sprint_name: str | None = None) -> dict:
    """
    Fetch Jira details for one person in a given team (board) and sprint.
    Either sprint_id or sprint_name must be provided.
    Returns { developer_name, team_name, sprint_name, sprint_id, issues[], issue_count, total_story_points, by_status }.
    """
    team_name = next((t for t, c in TEAMS_CONFIG.items() if c.get("board_id") == board_id), "Unknown")
    if not sprint_id and not sprint_name:
        return {
            "developer_name": developer_name,
            "team_name": team_name,
            "sprint_name": None,
            "sprint_id": None,
            "issues": [],
            "issue_count": 0,
            "total_story_points": 0,
            "by_status": {},
            "error": "Provide either sprint_id or sprint_name",
        }
    try:
        board_sp_field = get_board_story_points_field(board_id) or JIRA_STORY_POINTS_FIELD
        logger.info("get_person_details: developer=%s board_id=%s sprint_id=%s sprint_name=%s story_points_field=%s", developer_name, board_id, sprint_id, sprint_name, board_sp_field or "(none)")
        if sprint_id:
            jql = f'sprint = {sprint_id} AND assignee = "{developer_name}"'
        else:
            jql = f'sprint = "{sprint_name}" AND assignee = "{developer_name}"'
        if JIRA_PERSON_LOOKUP_JQL_FILTER:
            jql += f" AND {JIRA_PERSON_LOOKUP_JQL_FILTER}"
        fields = ["summary", "status", "assignee", "issuetype", "sprint", "parent"]
        if board_sp_field:
            fields.append(board_sp_field)
        issues = jira_client.search_issues(jql, maxResults=200, fields=",".join(fields))
        logger.info("get_person_details: JQL returned %s issues (fields requested: %s)", len(issues), ",".join(fields))
        status_to_bucket = get_board_status_to_bucket(board_id)
        resolved_sprint_name = sprint_name
        resolved_sprint_id = sprint_id
        if not resolved_sprint_name and issues and hasattr(issues[0].fields, "sprint"):
            try:
                sp = getattr(issues[0].fields, "sprint", None)
                if sp and isinstance(sp, (list, tuple)) and len(sp) > 0:
                    s = sp[0] if isinstance(sp[0], dict) else sp[0]
                    resolved_sprint_name = s.get("name") if isinstance(s, dict) else str(s)
                elif sp:
                    resolved_sprint_name = str(sp)
            except Exception:
                pass

        # Resolve parent story points for subtasks with 0 points (story points often live on parent)
        issue_keys_in_list = {i.key for i in issues}
        parent_keys_to_fetch = set()
        for i in issues:
            pts = _get_story_points(i, story_points_field=board_sp_field)
            if pts > 0:
                continue
            parent = getattr(i.fields, "parent", None)
            if parent is not None:
                parent_key = getattr(parent, "key", None) or (parent.get("key") if isinstance(parent, dict) else None)
                if parent_key and parent_key not in issue_keys_in_list:
                    parent_keys_to_fetch.add(parent_key)
        parent_points_cache = {}
        if parent_keys_to_fetch and board_sp_field:
            parent_fields = f"summary,{board_sp_field}"
            for pkey in parent_keys_to_fetch:
                try:
                    parent_issue = jira_client.issue(pkey, fields=parent_fields)
                    parent_points_cache[pkey] = _get_story_points(parent_issue, story_points_field=board_sp_field)
                except Exception as e:
                    logger.debug("Could not fetch parent %s for story points: %s", pkey, e)

        issues_list = []
        by_status = {}
        not_started_pts = in_progress_pts = completed_pts = blocked_pts = 0
        parents_already_counted = set()  # when parent not in list, we add its pts for one subtask only
        for i in issues:
            status_obj = i.fields.status
            status = status_obj.name if status_obj else "—"
            by_status[status] = by_status.get(status, 0) + 1
            status_id = getattr(status_obj, "id", None) if status_obj else None
            if status_id is None and status_obj is not None:
                raw_status = (getattr(i, "raw", None) or {}).get("fields", {}).get("status")
                status_id = raw_status.get("id") if isinstance(raw_status, dict) else None
            pts = _get_story_points(i, story_points_field=board_sp_field)
            parent = getattr(i.fields, "parent", None)
            parent_key = getattr(parent, "key", None) if parent and not isinstance(parent, dict) else (parent.get("key") if isinstance(parent, dict) else None)
            if pts == 0 and parent_key and parent_key in parent_points_cache:
                pts = parent_points_cache[parent_key]
            display_pts = pts
            # For total: count parent's points only once when parent is not in the list
            if pts > 0 and parent_key and parent_key not in issue_keys_in_list and parent_key in parent_points_cache:
                if parent_key in parents_already_counted:
                    pts = 0
                else:
                    parents_already_counted.add(parent_key)
            bucket = _bucket_for_status(status_id, status, status_to_bucket)
            if bucket == "not_started":
                not_started_pts += pts
            elif bucket == "in_progress":
                in_progress_pts += pts
            elif bucket == "blocked":
                blocked_pts += pts
            else:
                completed_pts += pts
            pts_display = int(display_pts) if isinstance(display_pts, (int, float)) and display_pts == int(display_pts) else display_pts
            issues_list.append({
                "key": i.key,
                "summary": (i.fields.summary or "")[:120],
                "status": status,
                "issuetype": i.fields.issuetype.name if i.fields.issuetype else "—",
                "story_points": pts_display,
                "url": f"{JIRA_SERVER}/browse/{i.key}",
            })
        total_story_points = not_started_pts + in_progress_pts + completed_pts + blocked_pts
        issues_with_points = sum(1 for x in issues_list if (x.get("story_points") or 0) > 0)
        logger.info("get_person_details: person_breakdown total=%s not_started=%s in_progress=%s completed=%s blocked=%s (issues_with_points=%s/%s)", total_story_points, not_started_pts, in_progress_pts, completed_pts, blocked_pts, issues_with_points, len(issues_list))
        if total_story_points == 0 and issues and board_sp_field:
            raw_fields = getattr(issues[0], "raw", None) or {}
            field_keys = list((raw_fields.get("fields") or {}).keys()) if isinstance(raw_fields.get("fields"), dict) else []
            logger.info("get_person_details: story points 0; first issue %s raw['fields'] keys (sample): %s (looking for %s)", issues[0].key, field_keys[:30], board_sp_field)
        person_breakdown = {
            "total": total_story_points,
            "not_started": not_started_pts,
            "in_progress": in_progress_pts,
            "completed": completed_pts,
            "blocked": blocked_pts,
        }
        sprint_breakdown = get_sprint_story_points_breakdown(
            board_id, sprint_id=resolved_sprint_id, sprint_name=resolved_sprint_name
        )
        logger.info("get_person_details: sprint_breakdown total=%s not_started=%s in_progress=%s completed=%s blocked=%s", sprint_breakdown.get("total"), sprint_breakdown.get("not_started"), sprint_breakdown.get("in_progress"), sprint_breakdown.get("completed"), sprint_breakdown.get("blocked"))
        return {
            "developer_name": developer_name,
            "team_name": team_name,
            "sprint_name": resolved_sprint_name or sprint_name,
            "sprint_id": resolved_sprint_id,
            "issues": issues_list,
            "issue_count": len(issues_list),
            "total_story_points": total_story_points,
            "by_status": by_status,
            "person_breakdown": person_breakdown,
            "sprint_breakdown": sprint_breakdown,
            "error": None,
        }
    except Exception as e:
        logger.exception("get_person_details failed")
        return {
            "developer_name": developer_name,
            "team_name": team_name,
            "sprint_name": sprint_name,
            "sprint_id": sprint_id,
            "issues": [],
            "issue_count": 0,
            "total_story_points": 0,
            "by_status": {},
            "person_breakdown": {"total": 0, "not_started": 0, "in_progress": 0, "completed": 0, "blocked": 0},
            "sprint_breakdown": {"total": 0, "not_started": 0, "in_progress": 0, "completed": 0, "blocked": 0},
            "error": str(e),
        }
