"""Jira verification process checkers for the daily report generator."""

import logging

logger = logging.getLogger(__name__)


def check_ac_plus_one_comment(jira_client, issue) -> bool:
   """Check if developer has added '+1' comment indicating AC understanding."""
   try:
       comments = jira_client.comments(issue.key)
       for comment in comments:
           if '+1' in comment.body.lower() or ('ac' in comment.body.lower() and '1' in comment.body):
               return True
       return False
   except Exception as e:
       logger.debug(f"Error checking +1 comment for {issue.key}: {e}")
       return False


def check_pr_link(issue) -> bool:
   """Check if PR is linked to the issue."""
   try:
       links = issue.fields.issuelinks
       for link in links:
           if 'pull request' in link.type.name.lower() or 'relates to' in link.type.name.lower():
               return True
       return False
   except Exception as e:
       logger.debug(f"Error checking PR link for {issue.key}: {e}")
       return False


def check_labels(issue) -> bool:
   """Check if labels are present on the issue."""
   try:
       return bool(issue.fields.labels and len(issue.fields.labels) > 0)
   except Exception as e:
       logger.debug(f"Error checking labels for {issue.key}: {e}")
       return False


def check_time_logged(jira_client, issue) -> bool:
   """Check if time has been logged (worklog exists)."""
   try:
       worklogs = jira_client.worklogs(issue.key)
       return bool(worklogs and len(worklogs) > 0)
   except Exception as e:
       logger.debug(f"Error checking time logged for {issue.key}: {e}")
       return False


def check_evidence_attached(issue) -> bool:
   """Check if attachments (evidence) are present."""
   try:
       attachments = issue.fields.attachment
       return bool(attachments and len(attachments) > 0)
   except Exception as e:
       logger.debug(f"Error checking attachments for {issue.key}: {e}")
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
   status = issue.fields.status.name.lower() if issue.fields.status else ""

   # In Progress+: Must have +1 AC comment
   if status in ['in progress', 'in review', 'in test', 'ready for production', 'resolved']:
       if not check_ac_plus_one_comment(jira_client, issue):
           violations.append("Missing +1 AC confirmation comment from developer")

   # In Review+: Must have PR link
   if status in ['in review', 'in test', 'ready for production', 'resolved']:
       if not check_pr_link(issue):
           violations.append("Missing PR link (Code Review step)")

   # In Test+: Must have labels, time logged, and evidence
   if status in ['in test', 'ready for production', 'resolved']:
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
       "is_compliant": len(violations) == 0
   }
