"""Jira sprint and agile report helpers (sprint info, spillover, velocity)."""
from datetime import datetime, timedelta
import fnmatch
import logging
import re
import requests

from config import jira_client, JIRA_SERVER, JIRA_USER_NAME, JIRA_TOKEN, JIRA_STORY_POINTS_FIELD

try:
    from team_config import JIRA_PERSON_LOOKUP_JQL_FILTER, JIRA_REPORT_ISSUE_TYPES_JQL
except ImportError:
    JIRA_PERSON_LOOKUP_JQL_FILTER = ""
    JIRA_REPORT_ISSUE_TYPES_JQL = 'issuetype IN ("Story", "Bug")'

from jira_api.jira_issues import _get_story_points

logger = logging.getLogger(__name__)


def _fmt_sprint_date(d: str, is_end_date: bool = False) -> str:
    """Format ISO date string for display (e.g. Feb 03, 2026). Handles midnight UTC end dates as previous day."""
    if not d:
        return "—"
    try:
        if "T" in d:
            dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
            if is_end_date and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                dt = dt - timedelta(days=1)
        else:
            dt = datetime.strptime(d[:10], "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except Exception:
        return d[:10] if len(d) >= 10 else d or "—"


def _sprint_end_date_display(sprint) -> str:
    """Get formatted end/complete date for a sprint object (matches Jira UI e.g. Feb 03, 2026)."""
    if not sprint:
        return "—"
    raw = getattr(sprint, "raw", None) or {}
    if isinstance(raw, dict):
        end_iso = raw.get("endDate") or raw.get("end") or raw.get("completeDate") or ""
    else:
        end_iso = getattr(sprint, "endDate", None) or getattr(sprint, "completeDate", None) or ""
    return _fmt_sprint_date(end_iso if isinstance(end_iso, str) else str(end_iso or ""), is_end_date=True)


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

    return {
        "name": name or "—",
        "id": sid,
        "startDate": _fmt_sprint_date(start_iso),
        "endDate": _fmt_sprint_date(end_iso, is_end_date=True),
    }


def get_active_sprint_info(board_id: int) -> dict | None:
    """Get active sprint info: name, id, startDate, endDate (display-formatted)."""
    try:
        sprints = jira_client.sprints(board_id, state='active')
        return _sprint_info_from_sprint(sprints[0]) if sprints else None
    except Exception as e:
        logger.error(f"Failed to fetch sprint info for board {board_id}: {e}")
        return None


def sprint_name_allowed(
    sprint_name: str,
    excluded_sprint_names: list[str],
    included_sprint_names: list[str] | None = None,
) -> bool:
    """True if sprint_name is not in excluded and matches included (if specified). Same rules as get_allowed_sprint_info."""
    excluded_set = {s.strip().lower() for s in (excluded_sprint_names or []) if s}
    included_list = [s.strip() for s in (included_sprint_names or []) if s]
    name = (sprint_name or "").strip()
    if name.lower() in excluded_set:
        return False
    if not included_list:
        return True
    for pattern in included_list:
        if "*" in pattern:
            if fnmatch.fnmatch(name, pattern):
                return True
        elif name == pattern:
            return True
    return False


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


def _sprint_complete_year(sprint) -> int | None:
    """Get completion year from sprint (completeDate or endDate) for filtering by year. Returns None if no date."""
    raw = getattr(sprint, "raw", None) or {}
    if isinstance(raw, dict):
        iso = (
            raw.get("completeDate") or raw.get("endDate") or raw.get("startDate")
            or raw.get("end") or raw.get("start") or ""
        )
    else:
        iso = getattr(sprint, "completeDate", None) or getattr(sprint, "endDate", None) or getattr(sprint, "startDate", None) or ""
    if iso is not None and not isinstance(iso, str):
        iso = str(iso)
    if not iso or len(iso) < 4:
        return None
    try:
        return int(iso[:4])
    except (ValueError, TypeError):
        return None


def _closed_sprints_current_year(sprints: list, current_year: int | None = None) -> list:
    """Filter closed sprints to those completed in current_year. Sorted by completeDate desc. If current_year is None, use datetime.now().year."""
    year = current_year if current_year is not None else datetime.now().year
    filtered = [s for s in sprints if _sprint_complete_year(s) == year]
    return sorted(filtered, key=lambda s: _sprint_complete_date(s), reverse=True)


def _jira_agile_auth() -> tuple[str, str] | None:
    if JIRA_USER_NAME and JIRA_TOKEN:
        return (JIRA_USER_NAME, JIRA_TOKEN)
    return None


def _agile_report_url(board_id: int, sprint_id: int) -> str:
    return f"{JIRA_SERVER.rstrip('/')}/rest/agile/1.0/board/{board_id}/sprint/{sprint_id}/report"


def _fetch_sprint_by_id(sprint_id: int) -> dict | None:
    """GET sprint by ID from Agile API. Returns raw dict with originBoardId, completeDate, etc., or None."""
    auth = _jira_agile_auth()
    if not auth:
        return None
    try:
        url = f"{JIRA_SERVER.rstrip('/')}/rest/agile/1.0/sprint/{sprint_id}"
        r = requests.get(url, auth=auth, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        logger.debug("[Spillover] _fetch_sprint_by_id sprint_id=%s: %s", sprint_id, e)
        return None


def _get_sprint_origin_board_id(sprint_id: int) -> int | None:
    """GET sprint by ID and return originBoardId. Sprint report often 404s when using a board that isn't the sprint's origin board."""
    data = _fetch_sprint_by_id(sprint_id)
    if not data:
        return None
    ob = data.get("originBoardId")
    return int(ob) if ob is not None else None


def _fetch_closed_sprints_agile(board_id: int, max_results: int = 300) -> list:
    """Fetch all closed sprints from Agile API (paginated). API caps at 50 per request; keep fetching until we have all or hit max_results so matching sprint on 3rd/4th/5th page is included. Sorted by completeDate descending (most recent first)."""
    auth = _jira_agile_auth()
    if not auth:
        return []
    out = []
    page_size = 50
    start_at = 0
    try:
        url = f"{JIRA_SERVER.rstrip('/')}/rest/agile/1.0/board/{board_id}/sprint"
        while start_at < max_results:
            r = requests.get(url, auth=auth, timeout=15, params={"state": "closed", "startAt": start_at, "maxResults": page_size})
            if r.status_code != 200:
                break
            data = r.json()
            values = data.get("values") or data.get("sprints") or []
            total = data.get("total", 0)
            for v in values:
                if not isinstance(v, dict):
                    continue
                raw = dict(v)
                sid = raw.get("id")
                name = raw.get("name") or str(sid or "")
                if sid is not None:
                    out.append(type("Sprint", (), {"id": int(sid), "name": name, "raw": raw})())
            if not values or start_at + len(values) >= total or len(values) < page_size:
                break
            start_at += len(values)
        out = sorted(out, key=lambda s: _sprint_complete_date(s), reverse=True)
    except Exception as e:
        logger.debug("[Spillover] _fetch_closed_sprints_agile board_id=%s: %s", board_id, e)
    return out


def _sprint_complete_year_from_api(sprint_id: int) -> int | None:
    """Get completion year for a sprint by fetching GET /rest/agile/1.0/sprint/{id}. Use when board list doesn't have parseable dates."""
    data = _fetch_sprint_by_id(sprint_id)
    if not data:
        return None
    iso = data.get("completeDate") or data.get("endDate") or data.get("startDate") or ""
    if not iso or len(iso) < 4:
        return None
    try:
        return int(str(iso)[:4])
    except (ValueError, TypeError):
        return None


# Max PI count for wrap-around: PI1 S1's previous sprint is PI4 S*
MAX_PI = 4


def derive_previous_sprint_name(sprint_name: str) -> str | None:
    """Derive the previous sprint for spillover. Same PI: PI2 S3 -> PI2 S2, PI2 S2 -> PI2 S1. When current is S1 (e.g. PI2 S1), previous is last sprint of previous PI (e.g. PI1 S*); PI1 S1 wraps to PI4 S*. Returns exact name or pattern 'PIx S* suffix' (max 4 PIs)."""
    if not (sprint_name or "").strip():
        return None
    m = re.match(r"^PI\s*(\d+)\s+S\s*(\d+)(\s.*)$", (sprint_name or "").strip(), re.IGNORECASE)
    if not m:
        return None
    pi_str, s_num_str, suffix = m.group(1), m.group(2), m.group(3)
    try:
        pi_num = int(pi_str)
        s_num = int(s_num_str)
    except ValueError:
        return None
    if s_num >= 2:
        return f"PI{pi_num} S{s_num - 1}{suffix}".strip()
    # S1: previous = last sprint of previous PI (wrap 1..MAX_PI)
    prev_pi = ((pi_num - 2 + MAX_PI) % MAX_PI) + 1
    return f"PI{prev_pi} S*{suffix}".strip()


def get_closed_sprint_info_by_name_and_year(
    board_id: int,
    sprint_name: str,
    year: int | None = None,
) -> dict | None:
    """Find a closed sprint on the board that completed in the given year. sprint_name can be exact (e.g. PI2 S2 Dig Exp) or a pattern (e.g. PI1 S* Dig Exp); for pattern, returns the most recently closed matching sprint (last sprint of that PI). Returns { id, name } or None."""
    if not (sprint_name or "").strip():
        return None
    target_year = year if year is not None else datetime.now().year
    name_clean = (sprint_name or "").strip()
    use_pattern = "*" in name_clean
    try:
        sprints = jira_client.sprints(board_id, state="closed")
        if not sprints:
            return None
        closed_in_year = _closed_sprints_current_year(sprints, target_year)
        # If no closed sprints in target year (e.g. Jira API returns dates we don't parse as 2026), fall back to all closed sprints when doing exact name match
        if closed_in_year:
            search_list: list = closed_in_year
        else:
            if use_pattern:
                return None
            # Exact name: use one Agile API call (desc order client-side); no pagination
            search_list = _fetch_closed_sprints_agile(board_id)
            if not search_list:
                search_list = sorted(sprints, key=lambda s: _sprint_complete_date(s), reverse=True)
        if use_pattern:
            matching = [
                s for s in search_list
                if fnmatch.fnmatch((getattr(s, "name", None) or str(s)).strip(), name_clean)
            ]
            if not matching:
                return None
            s = matching[0]
            name = (getattr(s, "name", None) or str(s)).strip()
            sid = getattr(s, "id", None)
            if sid is not None:
                return {"id": int(sid), "name": name, "endDate": _sprint_end_date_display(s)}
            return None
        # Exact name: collect all name matches, then prefer the one that closed in target_year (current year)
        name_matches = [
            s for s in search_list
            if (getattr(s, "name", None) or str(s)).strip() and
            ((getattr(s, "name", None) or str(s)).strip() == name_clean or (getattr(s, "name", None) or str(s)).strip().lower() == name_clean.lower())
        ]
        if not name_matches:
            return None
        # Only use sprint that completed in current year (same name exists in 2025 and 2026)
        in_target_year = [s for s in name_matches if _sprint_complete_year(s) == target_year]
        if not in_target_year and name_matches:
            # Board list may not have parseable dates; resolve year by fetching each sprint by ID (GET sprint returns completeDate)
            for s in name_matches:
                sid = getattr(s, "id", None)
                if sid is not None and _sprint_complete_year_from_api(int(sid)) == target_year:
                    in_target_year.append(s)
                    break
        chosen = in_target_year[0] if in_target_year else None
        if chosen is None:
            return None
        name = (getattr(chosen, "name", None) or str(chosen)).strip()
        sid = getattr(chosen, "id", None)
        if sid is not None:
            return {"id": int(sid), "name": name, "endDate": _sprint_end_date_display(chosen)}
        return None
    except Exception as e:
        logger.warning(f"[Spillover] get_closed_sprint_info_by_name_and_year failed for board_id={board_id}: {e}")
        return None


def get_last_closed_sprint_id(board_id: int) -> int | None:
    """Get the chronologically most recently closed sprint id (by completeDate)."""
    info = get_last_closed_sprint_info(board_id)
    return info["id"] if info else None


def get_last_closed_sprint_info(board_id: int, year: int | None = None) -> dict | None:
    """Get the single chronologically most recently closed sprint in the given year: { id, name }. Default year = current year so names like PI2 S3 Dig Exp don't match last year's sprint. Returns None if no closed sprint in that year."""
    try:
        sprints = jira_client.sprints(board_id, state='closed')
        if not sprints:
            return None
        sorted_sprints = _closed_sprints_current_year(sprints, year)
        if not sorted_sprints:
            return None
        s = sorted_sprints[0]
        name = getattr(s, "name", None) or str(s)
        sid = getattr(s, "id", None)
        if sid is None:
            return None
        logger.debug("[Spillover] last closed sprint (year filter) board_id=%s -> id=%s name=%s", board_id, sid, name)
        return {"id": int(sid), "name": name, "endDate": _sprint_end_date_display(s)}
    except Exception as e:
        logger.warning(f"[Spillover] get_last_closed_sprint_info failed for board_id={board_id}: {e}")
        return None


def get_last_closed_sprint_info_allowed(
    board_id: int,
    excluded_sprint_names: list[str],
    included_sprint_names: list[str] | None = None,
    year: int | None = None,
) -> dict | None:
    """Get the single chronologically most recently closed sprint in the given year that is allowed (not excluded, matches included). Default year = current year so names like PI2 S3 Dig Exp don't match last year. For Dig Experience: spillover is from the last PI2 S* Dig Exp closed this year only."""
    try:
        sprints = jira_client.sprints(board_id, state='closed')
        if not sprints:
            return None
        sorted_sprints = _closed_sprints_current_year(sprints, year)
        for s in sorted_sprints:
            name = getattr(s, "name", None) or str(s)
            if sprint_name_allowed(name, excluded_sprint_names, included_sprint_names):
                sid = getattr(s, "id", None)
                if sid is not None:
                    logger.debug("[Spillover] last closed *allowed* sprint (year filter) board_id=%s -> id=%s name=%s", board_id, sid, name)
                    return {"id": int(sid), "name": name, "endDate": _sprint_end_date_display(s)}
        return None
    except Exception as e:
        logger.warning(f"[Spillover] get_last_closed_sprint_info_allowed failed for board_id={board_id}: {e}")
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
        url = _agile_report_url(board_id, sprint_id)
        r = requests.get(url, auth=auth, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Sprint report board_id=%s sprint_id=%s failed: %s", board_id, sprint_id, e)
        return None


def _parse_spillover_from_report(data: dict) -> list[dict]:
    """Extract spillover issues list from sprint report response (Cloud/DC shapes)."""
    def _spillover_story_points(item: dict, fields: dict) -> float:
        """Best-effort extraction of story points from sprint report item."""
        direct = None
        if isinstance(fields, dict):
            direct = fields.get(JIRA_STORY_POINTS_FIELD)
            if direct is None and JIRA_STORY_POINTS_FIELD != "customfield_10016":
                direct = fields.get("customfield_10016")
            if direct is not None:
                try:
                    return float(direct)
                except (TypeError, ValueError):
                    pass

        for stat_key in ("estimateStatistic", "currentEstimateStatistic", "estimated"):
            stat = item.get(stat_key)
            if stat is None and isinstance(fields, dict):
                stat = fields.get(stat_key)
            if not isinstance(stat, dict):
                continue
            stat_val = stat.get("statFieldValue")
            if isinstance(stat_val, dict):
                stat_val = stat_val.get("value")
            if stat_val is None:
                stat_val = stat.get("value")
            try:
                if stat_val is not None:
                    return float(stat_val)
            except (TypeError, ValueError):
                continue
        return 0.0

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
            labels = fields.get("labels") if isinstance(fields, dict) else []
            label = "tech-debt" if labels and "tech-debt" in (labels if isinstance(labels, list) else []) else ""
            story_points = _spillover_story_points(item, fields if isinstance(fields, dict) else {})
            result.append({"key": key, "summary": summary, "assignee": assignee, "label": label, "story_points": story_points})
        else:
            result.append({"key": str(item), "summary": "", "assignee": "", "label": "", "story_points": 0.0})
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
    if use_report_jql:
        jql += f" AND {JIRA_REPORT_ISSUE_TYPES_JQL}"
    elif JIRA_PERSON_LOOKUP_JQL_FILTER:
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
    """Get spillover issues via Jira Agile Sprint Report API. Returns (spillover_list, try_jql_fallback). If report 404s (sprint not on this board), retry with sprint's originBoardId."""
    data = _fetch_agile_report(board_id, sprint_id)
    if data is None:
        origin_board = _get_sprint_origin_board_id(sprint_id)
        if origin_board is not None and origin_board != board_id:
            data = _fetch_agile_report(origin_board, sprint_id)
    if data is None:
        return [], True
    spillover = _parse_spillover_from_report(data)
    return spillover, False


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


def get_spillover_via_jql(sprint_id: int, board_id: int | None = None) -> list[dict]:
    """Fallback: get spillover via JQL when Sprint Report API returns 404 (e.g. Jira Cloud)."""
    try:
        jql = f'sprint = {sprint_id} AND resolution is empty AND {JIRA_REPORT_ISSUE_TYPES_JQL} ORDER BY status ASC'
        story_points_field = get_board_story_points_field(board_id) if board_id else None
        if not story_points_field:
            story_points_field = JIRA_STORY_POINTS_FIELD
        fields = ["key", "summary", "assignee", "labels"]
        if story_points_field:
            fields.append(story_points_field)
        issues = jira_client.search_issues(jql, maxResults=200, fields=fields)
        out = []
        for i in issues:
            labels = getattr(i.fields, "labels", None) or []
            label = "tech-debt" if labels and "tech-debt" in labels else ""
            out.append({
                "key": i.key,
                "summary": (i.fields.summary or ""),
                "assignee": (i.fields.assignee.displayName if i.fields.assignee else "") or "",
                "label": label,
                "story_points": _get_story_points(i, story_points_field=story_points_field),
            })
        return out
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
