"""Jira verification process checkers for the daily report generator."""

import re
import logging

logger = logging.getLogger(__name__)

CONCOURSE_URL_PATTERN = re.compile(r"concourse-ci\.hy-vee\.cloud/builds", re.I)


def _comment_body_to_plain(body) -> str:
    """Convert comment body to plain text; handles ADF (dict) or string from Jira Cloud."""
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return _adf_to_plain_text(body)
    return str(body)


def _comment_bodies(jira_client, issue) -> list[str]:
    """Return list of comment body strings (lowercased) for the issue."""
    try:
        comments = jira_client.comments(issue.key)
        out = []
        for c in comments:
            raw = getattr(c, "body", None)
            plain = _comment_body_to_plain(raw)
            out.append(plain.lower())
        return out
    except Exception as e:
        logger.debug("Error fetching comments for %s: %s", issue.key, e)
        return []


def _adf_to_plain_text(obj) -> str:
    """Recursively extract plain text from Atlassian Document Format (ADF) description."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        if obj.get("type") == "text" and "text" in obj:
            t = obj["text"]
            return t if isinstance(t, str) else (t.get("text") or "")
        return " ".join(_adf_to_plain_text(v) for v in (obj.get("content") or obj.values()))
    if isinstance(obj, list):
        return " ".join(_adf_to_plain_text(item) for item in obj)
    return str(obj)


def _description_text(issue) -> str:
    """Return issue description as plain text (lowercased). Handles ADF or string."""
    try:
        desc = getattr(issue.fields, "description", None) or ""
        if hasattr(desc, "strip"):
            return desc.strip().lower()
        if isinstance(desc, dict):
            return _adf_to_plain_text(desc).lower()
        return str(desc).lower()
    except Exception:
        return ""


def _all_text_to_search(jira_client, issue) -> str:
    """Combined description + comments for checklist text checks."""
    parts = [_description_text(issue)]
    parts.extend(_comment_bodies(jira_client, issue))
    return "\n".join(parts)


def check_ac_plus_one_comment(jira_client, issue) -> bool:
    """True if a comment contains '+1' or '+1 to ac/AC'."""
    try:
        for body in _comment_bodies(jira_client, issue):
            if "+1" in body:
                return True
        return False
    except Exception as e:
        logger.debug("Error checking +1 comment for %s: %s", issue.key, e)
        return False


def _link_type_name(link) -> str:
    """Get link type name from link object or dict."""
    if link is None:
        return ""
    if isinstance(link, dict):
        t = link.get("type") or {}
        return (t.get("name") or "").lower() if isinstance(t, dict) else str(t).lower()
    t = getattr(link, "type", None)
    return (getattr(t, "name", None) or "").lower() if t else ""


def check_pr_link(jira_client, issue) -> bool:
    """True if PR is linked via comment (PR: https or /pull/ URL), issuelinks, or remotelinks/Development."""
    for body in _comment_bodies(jira_client, issue):
        if re.search(r"\bpr\s*:\s*https?://", body) or re.search(r"draft\s+pr\s*:\s*https?://", body):
            return True
        if re.search(r"https?://[^\s]+/pull/\d+", body):
            return True
    try:
        for link in getattr(issue.fields, "issuelinks", None) or []:
            name = _link_type_name(link)
            if "pull request" in name or "relates to" in name:
                return True
    except Exception as e:
        logger.debug("Error checking issuelinks for %s: %s", issue.key, e)
    try:
        raw = getattr(issue, "raw", None) or {}
        fields = raw.get("fields") or {}
        remotelinks = fields.get("remotelinks") or fields.get("remote links") or []
        if isinstance(remotelinks, list):
            for rl in remotelinks:
                if not isinstance(rl, dict):
                    continue
                obj = rl.get("object") or rl
                url = (obj.get("url") or "") if isinstance(obj, dict) else ""
                title = (obj.get("title") or "") if isinstance(obj, dict) else ""
                s = (rl.get("relationship") or "") + " " + title + " " + url
                if "pull" in s.lower() or "/pull/" in url:
                    return True
        for _, val in fields.items():
            if val is None:
                continue
            if isinstance(val, list) and any(
                isinstance(x, dict) and ("pullRequest" in str(x) or "pull_request" in str(x).lower() or "/pull/" in str(x))
                for x in val
            ):
                return True
            if isinstance(val, dict) and ("pullRequest" in str(val) or "repository" in str(val).lower() or "/pull/" in str(val)):
                return True
    except Exception as e:
        logger.debug("Error checking development info for %s: %s", issue.key, e)
    return False


def check_time_logged(jira_client, issue) -> bool:
    """Check if time has been logged (worklog exists)."""
    try:
        worklogs = jira_client.worklogs(issue.key)
        return bool(worklogs and len(worklogs) > 0)
    except Exception as e:
        logger.debug("Error checking time logged for %s: %s", issue.key, e)
        return False


def check_evidence_attached(issue) -> bool:
    """Evidence/Dev Evidence for Ready for Production; Prod Evidence (or Evidence/Dev) for Resolved."""
    try:
        status = (getattr(issue.fields, "status", None) or "")
        status_name = (getattr(status, "name", None) or "").lower().strip()
        labels = getattr(issue.fields, "labels", None) or []
        label_set = {lb.lower().strip() for lb in labels if lb}
        if status_name == "ready for production":
            return bool(label_set & {"evidence", "dev evidence"})
        if status_name == "resolved":
            return bool(label_set & {"prod evidence", "evidence", "dev evidence"})
        return True
    except Exception as e:
        logger.debug("Error checking evidence labels for %s: %s", issue.key, e)
        return False


def check_branch_cleanup_mentioned(jira_client, issue) -> bool:
    """Check if Branch Cleanup is mentioned in description or comments."""
    text = _all_text_to_search(jira_client, issue)
    return "branch cleanup" in text or "branch clean-up" in text or "branch cleaned" in text


def check_concourse_link_pasted(jira_client, issue) -> bool:
    """True if concourse-ci.hy-vee.cloud/builds appears in description or comments."""
    text = _all_text_to_search(jira_client, issue)
    return bool(CONCOURSE_URL_PATTERN.search(text))


def check_rollback_strategy_mentioned(jira_client, issue) -> bool:
    """Check if rollback strategy is mentioned in description or comments."""
    text = _all_text_to_search(jira_client, issue)
    return "rollback" in text


def check_verification_checklist(jira_client, issue) -> dict:
    """Status-dependent compliance: In Progress → +1 AC only; In Test → +1 AC + PR link; Ready for Prod/Resolved → all 7 rules."""
    violations = []
    status = (issue.fields.status.name or "").lower().strip() if issue.fields.status else ""

    if status == "in progress":
        if not check_ac_plus_one_comment(jira_client, issue):
            violations.append("Missing +1 AC confirmation comment from developer")
        return _verification_result(issue, status, violations)

    if status == "in test":
        if not check_ac_plus_one_comment(jira_client, issue):
            violations.append("Missing +1 AC confirmation comment from developer")
        if not check_pr_link(jira_client, issue):
            violations.append("Missing PR link (Code Review step)")
        return _verification_result(issue, status, violations)

    if status in ("ready for production", "resolved"):
        if not check_ac_plus_one_comment(jira_client, issue):
            violations.append("Missing +1 AC confirmation comment from developer")
        if not check_pr_link(jira_client, issue):
            violations.append("Missing PR link (Code Review step)")
        if not check_branch_cleanup_mentioned(jira_client, issue):
            violations.append("Branch Cleanup not mentioned")
        if not check_concourse_link_pasted(jira_client, issue):
            violations.append("Concourse link not pasted in Jira (concourse-ci.hy-vee.cloud/builds)")
        if not check_rollback_strategy_mentioned(jira_client, issue):
            violations.append("Rollback strategy not mentioned")
        if not check_evidence_attached(issue):
            violations.append("No dev/local evidence attached (Post-Merge step)")
        if not check_time_logged(jira_client, issue):
            violations.append("No time logged (Post-Merge step)")
        return _verification_result(issue, status, violations)

    return _verification_result(issue, status, violations)


def _assignee_display(issue) -> str:
    """Assignee display name or 'Unassigned'."""
    try:
        assignee = getattr(issue.fields, "assignee", None)
        if assignee is None:
            return "Unassigned"
        if hasattr(assignee, "displayName") and assignee.displayName:
            return assignee.displayName
        raw = getattr(issue, "raw", None) or {}
        fields = raw.get("fields") or {}
        a = fields.get("assignee")
        if isinstance(a, dict) and a.get("displayName"):
            return a["displayName"]
        return "Unassigned"
    except Exception:
        return "Unassigned"


def _verification_result(issue, status: str, violations: list) -> dict:
    return {
        "issue_key": issue.key,
        "summary": issue.fields.summary,
        "status": status,
        "assignee": _assignee_display(issue),
        "violations": violations,
        "is_compliant": len(violations) == 0,
    }
