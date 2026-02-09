"""Jira verification process checkers for the daily report generator."""

import logging

logger = logging.getLogger(__name__)


def check_ac_plus_one_comment(jira_client, issue) -> bool:
    """Check if developer has added '+1' comment indicating AC understanding."""
    try:
        comments = jira_client.comments(issue.key)
        for comment in comments:
            body = (getattr(comment, "body", None) or "").lower()
            if "+1" in body or ("ac" in body and "1" in body):
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


def check_pr_link(issue) -> bool:
    """Check if PR is linked to the issue."""
    try:
        links = getattr(issue.fields, "issuelinks", None) or []
        for link in links:
            name = _link_type_name(link)
            if "pull request" in name or "relates to" in name:
                return True
        return False
    except Exception as e:
        logger.debug("Error checking PR link for %s: %s", issue.key, e)
        return False


def check_labels(issue) -> bool:
    """Check if labels are present on the issue."""
    try:
        labels = getattr(issue.fields, "labels", None)
        return bool(labels and len(labels) > 0)
    except Exception as e:
        logger.debug("Error checking labels for %s: %s", issue.key, e)
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
    """Check if attachments (evidence) are present."""
    try:
        attachments = getattr(issue.fields, "attachment", None)
        return bool(attachments and len(attachments) > 0)
    except Exception as e:
        logger.debug("Error checking attachments for %s: %s", issue.key, e)
        return False


def check_verification_checklist(jira_client, issue) -> dict:
    """Check Jira verification process compliance for a ticket based on status.

    Returns a dict with:
    - issue_key: The Jira key
    - summary: Issue summary
    - status: Current issue status
    - violations: List of verification process violations
    - is_compliant: Boolean indicating if all checks pass
    """
    violations = []
    status = (issue.fields.status.name or "").lower() if issue.fields.status else ""

    if status in ("in progress", "in review", "in test", "ready for production", "resolved"):
        if not check_ac_plus_one_comment(jira_client, issue):
            violations.append("Missing +1 AC confirmation comment from developer")

    if status in ("in review", "in test", "ready for production", "resolved"):
        if not check_pr_link(issue):
            violations.append("Missing PR link (Code Review step)")

    if status in ("in test", "ready for production", "resolved"):
        if not check_labels(issue):
            violations.append("Missing labels (Post-Merge step)")
        if not check_time_logged(jira_client, issue):
            violations.append("No time logged (Post-Merge step)")
        if not check_evidence_attached(issue):
            violations.append("No dev/local evidence attached (Post-Merge step)")

    return {
        "issue_key": issue.key,
        "summary": issue.fields.summary,
        "status": status,
        "violations": violations,
        "is_compliant": len(violations) == 0,
    }
