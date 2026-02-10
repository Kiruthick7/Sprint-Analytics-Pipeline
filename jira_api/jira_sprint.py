"""Jira sprint and agile report helpers (sprint info, spillover, velocity)."""
from datetime import datetime
import fnmatch
import logging
import re
import requests

from config import jira_client, JIRA_SERVER, JIRA_USER_NAME, JIRA_TOKEN, JIRA_STORY_POINTS_FIELD

try:
    from team_config import JIRA_PERSON_LOOKUP_JQL_FILTER
except ImportError:
    JIRA_PERSON_LOOKUP_JQL_FILTER = ""

from jira_api.jira_issues import _get_story_points

logger = logging.getLogger(__name__)


def _sprint_info_from_sprint(sprint) -> dict | None:
    """Build { name, id, startDate, endDate } from a sprint object. Dates from raw (ISO); formatted for display."""
    if not sprint:
        return None
    raw = getattr(sprint, "raw", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    name = getattr(sprint, "name", None) or str(sprint)
    sid = getattr(sprint, "id", None)
    if sid is not None:
        sid = int(sid)
    start_iso = raw.get("startDate") or raw.get("start") or ""
    end_iso = raw.get("endDate") or raw.get("end") or raw.get("completeDate") or ""

    def fmt(d: str) -> str:
        if not d:
            return "—"
        try:
            if "T" in d:
                dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(d[:10], "%Y-%m-%d")
            return dt.strftime("%b %d, %Y")
        except Exception:
            return d[:10] if len(d) >= 10 else d or "—"

    return {
        "name": name or "—",
        "id": sid,
        "startDate": fmt(start_iso),
        "endDate": fmt(end_iso),
    }


def get_active_sprint_info(board_id: int) -> dict | None:
    """Get active sprint info: name, id, startDate, endDate (display-formatted)."""
    try:
        sprints = jira_client.sprints(board_id, state='active')
        return _sprint_info_from_sprint(sprints[0]) if sprints else None
    except Exception as e:
        logger.error(f"Failed to fetch sprint info for board {board_id}: {e}")
        return None


def get_allowed_sprint_info(
    board_id: int,
    excluded_sprint_names: list[str],
    included_sprint_names: list[str] | None = None,
) -> dict | None:
    """When the board's active sprint is excluded, find an allowed sprint and return its info."""
    excluded_set = {s.strip().lower() for s in excluded_sprint_names if s}
    included_list = [s.strip() for s in (included_sprint_names or []) if s]

    def is_excluded(name: str) -> bool:
        return (name or "").strip().lower() in excluded_set

    def is_included(name: str) -> bool:
        if not included_list:
            return True
        name = (name or "").strip()
        for pattern in included_list:
            if "*" in pattern:
                if fnmatch.fnmatch(name, pattern):
                    return True
            elif name == pattern:
                return True
        return False

    try:
        for state in ("active", "future"):
            sprints = jira_client.sprints(board_id, state=state)
            for s in sprints:
                name = getattr(s, "name", None) or str(s)
                if not is_excluded(name) and is_included(name):
                    logger.info(f"Using sprint '{name}' (state={state}) for board {board_id} (excluded active was skipped)")
                    return _sprint_info_from_sprint(s)
        return None
    except Exception as e:
        logger.debug(f"Failed to get allowed sprint info for board {board_id}: {e}")
        return None


def _sprint_complete_date(sprint) -> str:
    """Get completeDate for sorting; prefer completeDate then endDate from raw."""
    raw = getattr(sprint, "raw", None) or {}
    if isinstance(raw, dict):
        return raw.get("completeDate") or raw.get("endDate") or ""
    return getattr(sprint, "completeDate", None) or getattr(sprint, "endDate", None) or ""


def _jira_agile_auth() -> tuple[str, str] | None:
    if JIRA_USER_NAME and JIRA_TOKEN:
        return (JIRA_USER_NAME, JIRA_TOKEN)
    return None


def _agile_report_url(board_id: int, sprint_id: int) -> str:
    return f"{JIRA_SERVER.rstrip('/')}/rest/agile/1.0/board/{board_id}/sprint/{sprint_id}/report"


def get_last_closed_sprint_id(board_id: int) -> int | None:
    """Get the chronologically most recently closed sprint id (by completeDate)."""
    try:
        sprints = jira_client.sprints(board_id, state='closed')
        if not sprints:
            return None
        sorted_sprints = sorted(sprints, key=lambda s: _sprint_complete_date(s), reverse=True)
        last_id = int(sorted_sprints[0].id)
        logger.debug("[Spillover] last closed sprint board_id=%s -> id=%s", board_id, last_id)
        return last_id
    except Exception as e:
        logger.warning(f"[Spillover] get_last_closed_sprint_id failed for board_id={board_id}: {e}")
        return None


def _agile_get(board_id: int, path: str, params: dict | None = None) -> dict | None:
    """GET from Jira Agile API (same auth as sprint report). Returns None on non-200 or error."""
    auth = _jira_agile_auth()
    if not auth:
        return None
    url = f"{JIRA_SERVER.rstrip('/')}/rest/agile/1.0/board/{board_id}{path}"
    try:
        r = requests.get(url, auth=auth, timeout=15, params=params or {})
        if r.status_code != 200:
            logger.debug("Agile API %s -> %s (body: %s)", url, r.status_code, r.text[:200] if r.text else "")
            return None
        return r.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Agile API %s failed: %s", url, e)
        return None


def get_board_backlog_count(board_id: int) -> int | None:
    """Return number of issues in the board's backlog (not in any sprint), or None if unavailable."""
    data = _agile_get(board_id, "/backlog", params={"maxResults": 0})
    if not data:
        return None
    total = data.get("total")
    if total is not None:
        return int(total)
    issues = data.get("issues") or data.get("values") or []
    return len(issues)


# Cache board config, story-points field, and status→bucket per board_id (avoids repeated API calls and log noise).
_board_config_cache: dict[int, dict | None] = {}
_board_sp_field_cache: dict[int, str | None] = {}  # None = not found, "" = use default
_board_status_to_bucket_cache: dict[int, dict[str, str]] = {}


def _get_board_config(board_id: int) -> dict | None:
    """Get board configuration (cached). One Agile API call per board per process."""
    if board_id not in _board_config_cache:
        _board_config_cache[board_id] = _agile_get(board_id, "/configuration")
    out = _board_config_cache[board_id]
    return out if isinstance(out, dict) else None


def get_board_story_points_field(board_id: int) -> str | None:
    """Get the story points custom field ID used by this board (cached). Returns None if not found."""
    if board_id in _board_sp_field_cache:
        return _board_sp_field_cache[board_id]
    data = _get_board_config(board_id)
    if not data:
        _board_sp_field_cache[board_id] = None
        return None
    est = data.get("estimation")
    fid = None
    if isinstance(est, dict) and est.get("type") == "field":
        field = est.get("field")
        if isinstance(field, dict):
            fid = field.get("fieldId")
    if fid:
        logger.debug("get_board_story_points_field: board_id=%s -> %s", board_id, fid)
        _board_sp_field_cache[board_id] = fid
        return fid
    logger.debug("get_board_story_points_field: board_id=%s -> not found", board_id)
    _board_sp_field_cache[board_id] = None
    return None


def get_board_status_to_bucket(board_id: int) -> dict[str, str]:
    """
    Map status id -> 'not_started' | 'in_progress' | 'completed' | 'blocked' from board column config (cached).
    First column = not_started, last column = completed; columns named Blocked/On hold = blocked; rest = in_progress.
    """
    if board_id in _board_status_to_bucket_cache:
        return _board_status_to_bucket_cache[board_id]
    data = _get_board_config(board_id)
    if not data:
        _board_status_to_bucket_cache[board_id] = {}
        return {}
    col_config = data.get("columnConfig")
    if not isinstance(col_config, dict):
        _board_status_to_bucket_cache[board_id] = {}
        return {}
    columns = col_config.get("columns")
    if not isinstance(columns, list) or not columns:
        _board_status_to_bucket_cache[board_id] = {}
        return {}
    out = {}
    for idx, col in enumerate(columns):
        if not isinstance(col, dict):
            continue
        statuses = col.get("statuses")
        if not isinstance(statuses, list):
            continue
        col_name = (col.get("name") or "").lower()
        if idx == 0:
            bucket = "not_started"
        elif idx == len(columns) - 1:
            bucket = "completed"
        elif "blocked" in col_name or "on hold" in col_name or "onhold" in col_name:
            bucket = "blocked"
        else:
            bucket = "in_progress"
        for st in statuses:
            if isinstance(st, dict) and st.get("id") is not None:
                out[str(st["id"])] = bucket
    logger.debug("get_board_status_to_bucket: board_id=%s -> %s statuses in %s columns", board_id, len(out), len(columns))
    _board_status_to_bucket_cache[board_id] = out
    return out


def _fetch_agile_report(board_id: int, sprint_id: int) -> dict | None:
    """GET sprint report JSON; returns None on 404, non-200, or request error."""
    auth = _jira_agile_auth()
    if not auth:
        return None
    try:
        r = requests.get(_agile_report_url(board_id, sprint_id), auth=auth, timeout=30)
        if r.status_code != 200:
            logger.debug("Sprint report board_id=%s sprint_id=%s -> %s (body: %s)", board_id, sprint_id, r.status_code, r.text[:200] if r.text else "")
            return None
        return r.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Sprint report board_id=%s sprint_id=%s failed: %s", board_id, sprint_id, e)
        return None


def _parse_spillover_from_report(data: dict) -> list[dict]:
    """Extract spillover issues list from sprint report response (Cloud/DC shapes)."""
    issues_raw = None
    if isinstance(data.get("contents"), dict):
        c = data["contents"]
        issues_raw = c.get("issuesNotCompletedInCurrentSprint") or c.get("issueNotCompletedInCurrentSprint")
    if issues_raw is None:
        issues_raw = data.get("issuesNotCompletedInCurrentSprint") or data.get("issueNotCompletedInCurrentSprint")
    if not issues_raw:
        return []
    issues_list = issues_raw["issues"] if isinstance(issues_raw, dict) and "issues" in issues_raw else (issues_raw if isinstance(issues_raw, list) else [])
    result = []
    for item in issues_list:
        if isinstance(item, dict):
            key = item.get("key") or (item.get("id") and str(item.get("id")))
            fields = item.get("fields") or item
            summary = (fields.get("summary") or "") if isinstance(fields, dict) else ""
            assignee_raw = fields.get("assignee") if isinstance(fields, dict) else None
            assignee = (assignee_raw.get("displayName") or "") if isinstance(assignee_raw, dict) else ""
            result.append({"key": key, "summary": summary, "assignee": assignee})
        else:
            result.append({"key": str(item), "summary": "", "assignee": ""})
    return result


def _parse_velocity_from_report(data: dict) -> str:
    """Extract completed points from sprint report (contents or top-level)."""
    contents = data.get("contents") if isinstance(data, dict) else None
    if isinstance(contents, dict):
        val = contents.get("completedIssuesEstimateSum") or contents.get("completedIssuesEstimate")
        if val is not None:
            return str(int(val)) if isinstance(val, (int, float)) else str(val)
    val = data.get("completedIssuesEstimateSum") or data.get("completedIssuesEstimate") if isinstance(data, dict) else None
    if val is not None:
        return str(int(val)) if isinstance(val, (int, float)) else str(val)
    return "—"


def _parse_estimation_from_report(data: dict) -> str:
    """Extract committed/estimated points from sprint report if available."""
    contents = data.get("contents") if isinstance(data, dict) else None
    if isinstance(contents, dict):
        for key in ("allIssuesEstimateSum", "totalCommittedPoints", "estimateSum", "committedPoints"):
            val = contents.get(key)
            if val is not None:
                return str(int(val)) if isinstance(val, (int, float)) else str(val)
    for key in ("allIssuesEstimateSum", "totalCommittedPoints") if isinstance(data, dict) else []:
        val = data.get(key)
        if val is not None:
            return str(int(val)) if isinstance(val, (int, float)) else str(val)
    return "—"


def get_sprint_report_points_breakdown(board_id: int, sprint_id: int) -> dict | None:
    """Get sprint-level story points from Jira Agile Sprint Report (commitment + completed). Returns None if report unavailable."""
    data = _fetch_agile_report(board_id, sprint_id)
    if data is None:
        logger.debug("get_sprint_report_points_breakdown: board_id=%s sprint_id=%s report unavailable", board_id, sprint_id)
        return None
    total_raw = _parse_estimation_from_report(data)
    completed_raw = _parse_velocity_from_report(data)
    try:
        total = float(total_raw) if total_raw != "—" else 0.0
        completed = float(completed_raw) if completed_raw != "—" else 0.0
    except (TypeError, ValueError):
        logger.debug("get_sprint_report_points_breakdown: board_id=%s sprint_id=%s parse failed (total_raw=%s completed_raw=%s)", board_id, sprint_id, total_raw, completed_raw)
        return None
    logger.debug("get_sprint_report_points_breakdown: board_id=%s sprint_id=%s -> total=%s completed=%s", board_id, sprint_id, total, completed)
    return {"total": total, "completed": completed}


def _is_resolved(issue) -> bool:
    """True if the issue has a resolution set (resolved/done in Jira)."""
    res = getattr(issue.fields, "resolution", None)
    if res is not None:
        return True
    raw = getattr(issue, "raw", None) or {}
    fields_dict = raw.get("fields") if isinstance(raw, dict) else {}
    if isinstance(fields_dict, dict):
        res = fields_dict.get("resolution")
        if res is not None:
            return True
    return False


def _is_subtask(issue) -> bool:
    """True if the issue is a subtask (so we don't double-count story points with parent)."""
    it = getattr(issue.fields, "issuetype", None)
    if it is not None:
        if getattr(it, "subtask", None) is True:
            return True
        name = getattr(it, "name", None) or (it.get("name") if isinstance(it, dict) else None)
        if name and str(name).lower() in ("subtask", "sub-task", "sub task"):
            return True
    raw = getattr(issue, "raw", None) or {}
    fields_dict = raw.get("fields") if isinstance(raw, dict) else {}
    if isinstance(fields_dict, dict):
        it = fields_dict.get("issuetype")
        if isinstance(it, dict) and it.get("subtask") is True:
            return True
        if isinstance(it, dict):
            name = it.get("name", "")
            if str(name).lower() in ("subtask", "sub-task", "sub task"):
                return True
    return False


def get_sprint_story_points_from_issues(
    board_id: int,
    sprint_id: int | None = None,
    sprint_name: str | None = None,
    *,
    use_report_jql: bool = False,
) -> dict[str, float]:
    """Sum total and completed story points from issues in the sprint (fallback when Sprint Report API returns 404).
    When use_report_jql=True: no JQL filter — fetch all issues in sprint; Commitment = parent issues' story points only,
    Completed = parent issues that are resolved (so values match Jira velocity report).
    Otherwise uses person-lookup JQL and board column for completed."""
    out: dict[str, float] = {"total": 0.0, "completed": 0.0}
    if sprint_id:
        jql = f"sprint = {sprint_id}"
    elif sprint_name:
        jql = f'sprint = "{sprint_name}"'
    else:
        return out
    if not use_report_jql and JIRA_PERSON_LOOKUP_JQL_FILTER:
        jql += f" AND {JIRA_PERSON_LOOKUP_JQL_FILTER}"
    sp_field = get_board_story_points_field(board_id) or JIRA_STORY_POINTS_FIELD
    status_to_bucket = get_board_status_to_bucket(board_id) if not use_report_jql else {}
    fields = ["status", "resolution", "issuetype"]
    if sp_field:
        fields.append(sp_field)
    try:
        issues = jira_client.search_issues(jql, maxResults=500, fields=",".join(fields))
        for i in issues:
            if use_report_jql and _is_subtask(i):
                continue
            pts = _get_story_points(i, story_points_field=sp_field)
            out["total"] += pts
            if use_report_jql:
                if _is_resolved(i):
                    out["completed"] += pts
            else:
                status_obj = i.fields.status
                status_id = getattr(status_obj, "id", None) if status_obj else None
                if status_id is None and status_obj is not None:
                    raw_status = (getattr(i, "raw", None) or {}).get("fields", {}).get("status")
                    status_id = raw_status.get("id") if isinstance(raw_status, dict) else None
                bucket = status_to_bucket.get(str(status_id), "in_progress") if status_id is not None else "in_progress"
                if bucket == "completed":
                    out["completed"] += pts
    except Exception as e:
        logger.warning("get_sprint_story_points_from_issues: %s", e)
    return out


def get_sprint_total_story_points(
    board_id: int, sprint_id: int, sprint_name: str | None = None, *, use_report_jql: bool = False
) -> str | None:
    """Get total story points in sprint from Agile Sprint Report; if unavailable, sum from issues."""
    data = _fetch_agile_report(board_id, sprint_id)
    if data is not None:
        raw = _parse_estimation_from_report(data)
        if raw != "—":
            return raw
    fallback = get_sprint_story_points_from_issues(
        board_id, sprint_id=sprint_id, sprint_name=sprint_name, use_report_jql=use_report_jql
    )
    total = fallback.get("total") or 0
    if total == 0:
        return None
    return str(int(total)) if total == int(total) else str(total)


def get_sprint_report_spillover(board_id: int, sprint_id: int) -> tuple[list[dict], bool]:
    """Get spillover issues via Jira Agile Sprint Report API. Returns (spillover_list, try_jql_fallback)."""
    data = _fetch_agile_report(board_id, sprint_id)
    if data is None:
        return [], True
    return _parse_spillover_from_report(data), False


def get_sprint_velocity(board_id: int, sprint_id: int) -> str:
    """Get velocity (completed story points) from Agile Sprint Report API; returns '—' if unavailable."""
    data = _fetch_agile_report(board_id, sprint_id)
    if data is None:
        return "—"
    return _parse_velocity_from_report(data)


def get_velocity_history(board_id: int, num_sprints: int = 5) -> list[dict]:
    """Get last N closed sprints with commitment and completed story points for inline velocity table.
    Uses Sprint Report API when available; falls back to summing issues when report returns 404.
    Returns list of {sprint_name, commitment, completed} (completed as float for averaging).
    """
    out = []
    try:
        sprints = jira_client.sprints(board_id, state="closed")
        if not sprints:
            return out
        sorted_sprints = sorted(sprints, key=lambda s: _sprint_complete_date(s), reverse=True)
        for s in sorted_sprints[:num_sprints]:
            sid = getattr(s, "id", None)
            name = getattr(s, "name", None) or str(s)
            if sid is None:
                continue
            data = _fetch_agile_report(board_id, int(sid))
            if data is not None:
                commitment = _parse_estimation_from_report(data)
                completed = _parse_velocity_from_report(data)
                commitment_str = commitment if commitment != "—" else "—"
                completed_str = completed if completed != "—" else "—"
                completed_num = None
                if completed != "—":
                    try:
                        completed_num = float(completed)
                    except (TypeError, ValueError):
                        pass
            else:
                fallback = get_sprint_story_points_from_issues(
                    board_id, sprint_id=int(sid), sprint_name=name, use_report_jql=True
                )
                total_f = fallback.get("total") or 0
                comp_f = fallback.get("completed") or 0
                commitment_str = str(int(total_f)) if total_f == int(total_f) else str(total_f) if total_f else "—"
                completed_str = str(int(comp_f)) if comp_f == int(comp_f) else str(comp_f) if comp_f else "—"
                completed_num = float(comp_f) if comp_f else None
            out.append({
                "sprint_name": name,
                "commitment": commitment_str,
                "completed": completed_str,
                "completed_num": completed_num,
            })
    except Exception as e:
        logger.warning("get_velocity_history failed for board_id=%s: %s", board_id, e)
    return out


def get_spillover_via_jql(sprint_id: int) -> list[dict]:
    """Fallback: get spillover via JQL when Sprint Report API returns 404 (e.g. Jira Cloud)."""
    try:
        jql = f'sprint = {sprint_id} AND resolution is empty AND issuetype NOT IN subTaskIssueTypes() ORDER BY status ASC'
        issues = jira_client.search_issues(jql, maxResults=200, fields=["key", "summary", "assignee"])
        return [
            {
                "key": i.key,
                "summary": (i.fields.summary or ""),
                "assignee": (i.fields.assignee.displayName if i.fields.assignee else "") or "",
            }
            for i in issues
        ]
    except Exception as e:
        logger.warning(f"[Spillover] get_spillover_via_jql failed for sprint_id={sprint_id}: {e}")
        return []


def validate_sprint_name_format(sprint_name: str) -> tuple[bool, str]:
    """Validate sprint naming convention: PI<X> S<X> Squad_Name"""
    if not sprint_name:
        return False, "Sprint name is empty"
    if re.match(r"^PI\d+ S\d+ .+$", sprint_name):
        return True, ""
    return False, "Expected format: PI<X> S<X> Squad_Name (e.g., PI1 S1 Ecom Exp)"
