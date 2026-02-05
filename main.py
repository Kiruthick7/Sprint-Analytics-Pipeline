from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any
from jira import JIRA
import os
import re
from urllib.parse import quote
import markdown
import requests
import time
from langchain_core.runnables import RunnableSequence
from langchain_core.prompts import PromptTemplate
import logging
from dotenv import load_dotenv
import functions_framework
from team_config import TEAMS_CONFIG, REPORT_CHANNEL_WEBHOOK, JIRA_CUSTOM_JQL_FILTER
from verification_helpers import check_verification_checklist

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables for local testing
load_dotenv()

# Confluence config
CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "https://hy-vee.atlassian.net/wiki")
CONFLUENCE_USER = os.getenv("CONFLUENCE_USER", "")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_TOKEN", "")
CONFLUENCE_SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "")

# Setup Jira client
JIRA_SERVER = "https://hy-vee.atlassian.net"
JIRA_USER_NAME = os.getenv("JIRA_USER_NAME")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

# Validate environment variables
if not JIRA_USER_NAME or not JIRA_TOKEN:
   logger.error("Missing environment variables: JIRA_USER_NAME or JIRA_TOKEN")
   raise ValueError("Missing environment variables: JIRA_USER_NAME and JIRA_TOKEN are required")

if not REPORT_CHANNEL_WEBHOOK:
    logger.warning("TEAMS_WEBHOOK not set - report link will not be sent to Teams")

logger.info(f"Connecting to JIRA server: {JIRA_SERVER}")
logger.info(f"Using JIRA user: {JIRA_USER_NAME}")
logger.info(f"Teams configured: {list(TEAMS_CONFIG.keys())}")

try:
   jira_client = JIRA(server=JIRA_SERVER, basic_auth=(JIRA_USER_NAME, JIRA_TOKEN))
except Exception as e:
   logger.error(f"Failed to initialize Jira client: {e}")
   raise

# LLM: groq (free tier, higher TPM model) or gemini (free, large context; easy migration to Vertex AI later)
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "groq").strip().lower()
try:
    if LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        _api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not _api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY required for LLM_PROVIDER=gemini")
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            temperature=0.2,
            max_tokens=8192,
            google_api_key=_api_key,
        )
        logger.info("Using LLM: Gemini (gemini-1.5-flash, 1M context)")
    else:
        from langchain_groq import ChatGroq
        # llama-4-scout-17b: 30K TPM free tier (vs 6K for llama-3.1-8b), 131K context
        llm = ChatGroq(  # type: ignore[call-arg]
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.2,
            max_tokens=4096,
        )
        logger.info("Using LLM: Groq (llama-4-scout-17b, 30K TPM)")
except Exception as e:
    logger.error(f"Failed to initialize LLM: {e}")
    raise

# Define prompt for daily scrum report
daily_scrum_template = PromptTemplate(
   input_variables=["team_name", "sprint_name", "sprint_features", "stale_issues"],
   template="""
You are a professional scrum master analyzing the current sprint for Team: {team_name}.
Sprint: {sprint_name}

You are provided with a list of Jira issues including issue key, summary, status, assignee, reporter, and recent comments.

### Overall Progress and Completion Status
- Summarize overall progress with key metrics and completion status

### Blockers, Delays, or Issues Needing Attention
- List each blocker with issue key and brief description (if any exist)
- If no blockers, state "No blockers identified"

### Active Areas
- Mention active work areas with relevant issue keys

### Stagnant or Stale Issues
- List stagnant issues with brief descriptions and assignees
- If none, state "No stagnant issues identified"

### Risks and Areas Needing Updates
- Highlight risks, dependencies, and areas needing attention

### Notable Achievements
- List notable achievements with issue keys (if any exist)
- If no major achievements, state "Work in progress across multiple areas"

### Issues with Missing or Stale Updates (older than 3 days)
{stale_issues}

---

## Issues Table

**Legend:** ✅ = Verification checklist passed | ⚠️ = Verification issues detected

| Issue Key | Summary | Status | Assignee | Reporter | Recent Comment Summary | Action Items |
|-----------|---------|--------|----------|----------|------------------------|--------------|

Guidelines:
- The `Recent Comment Summary` column should include a brief summary of the last 1-2 comments (use bullet points or line breaks for clarity)
- The `Action Items` column should include next steps or requests for updates if relevant; leave blank if not applicable
- If an issue has no comments or the last comment is older than 3 days, tag the assignee in the action items to add a comment/update

Preserve the markdown formatting and column alignment. Show it as a clean table.
Do not include code or SQL interpretation.
If there is insufficient detail to summarize, clearly state what is missing and ask for updates.

{sprint_features}
   """
)


# Use RunnableSequence for LLM
sprint_chain = RunnableSequence(daily_scrum_template | llm)


def get_active_sprint_name(board_id: int) -> str | None:
   """Get the active sprint name for a specific board."""
   try:
       sprints = jira_client.sprints(board_id, state='active')
       return sprints[0].name if sprints else None
   except Exception as e:
       logger.error(f"Failed to fetch sprints for board {board_id}: {e}")
       return None


def get_allowed_sprint_name(
   board_id: int,
   excluded_sprint_names: list[str],
   included_sprint_names: list[str] | None = None,
) -> str | None:
   """
   When the board's active sprint is excluded, find an allowed sprint to use (active or future).
   Prefers sprints in included_sprint_names if given; otherwise first non-excluded sprint.
   """
   excluded_set = {s.strip().lower() for s in excluded_sprint_names if s}
   included_list = [s.strip() for s in (included_sprint_names or []) if s]

   def is_excluded(name: str) -> bool:
       return (name or "").strip().lower() in excluded_set

   def is_included(name: str) -> bool:
       if not included_list:
           return True
       return (name or "").strip() in included_list

   try:
       # Prefer active sprints first, then future
       for state in ("active", "future"):
           sprints = jira_client.sprints(board_id, state=state)
           for s in sprints:
               name = getattr(s, "name", None) or str(s)
               if not is_excluded(name) and is_included(name):
                   logger.info(f"Using sprint '{name}' (state={state}) for board {board_id} (excluded active was skipped)")
                   return name
       return None
   except Exception as e:
       logger.debug(f"Failed to get allowed sprint for board {board_id}: {e}")
       return None


def _sprint_complete_date(sprint) -> str:
   """Get completeDate for sorting; prefer completeDate then endDate from raw."""
   raw = getattr(sprint, "raw", None) or {}
   if isinstance(raw, dict):
       return raw.get("completeDate") or raw.get("endDate") or ""
   return getattr(sprint, "completeDate", None) or getattr(sprint, "endDate", None) or ""


def get_last_closed_sprint_id(board_id: int) -> int | None:
   """Get the chronologically most recently closed sprint id (by completeDate). API order is not guaranteed."""
   logger.info(f"[Spillover] get_last_closed_sprint_id: board_id={board_id}")
   try:
       sprints = jira_client.sprints(board_id, state='closed')
       logger.info(f"[Spillover] get_last_closed_sprint_id: board_id={board_id} -> closed sprints count={len(sprints) if sprints else 0}")
       if not sprints:
           logger.info(f"[Spillover] get_last_closed_sprint_id: board_id={board_id} -> no closed sprints, returning None")
           return None
       # Sort by completeDate descending so most recently closed is first (API order is not by date)
       sorted_sprints = sorted(
           sprints,
           key=lambda s: _sprint_complete_date(s),
           reverse=True,
       )
       last = sorted_sprints[0]
       last_id = int(last.id)
       last_name = getattr(last, "name", None) or "?"
       last_date = _sprint_complete_date(last)
       logger.info(f"[Spillover] get_last_closed_sprint_id: board_id={board_id} -> last closed sprint id={last_id} name={last_name} completeDate={last_date}")
       return last_id
   except Exception as e:
       logger.warning(f"[Spillover] get_last_closed_sprint_id failed for board_id={board_id}: {e}")
       return None


def get_sprint_report_spillover(board_id: int, sprint_id: int) -> tuple[list[dict], bool]:
   """Get spillover issues (not completed in sprint) via Jira Agile Sprint Report API.

   Endpoint: GET /rest/agile/1.0/board/{boardId}/sprint/{sprintId}/report (Data Center; returns 404 on Cloud).
   Returns (spillover_list, try_jql_fallback). Use try_jql_fallback when API returns 404 (e.g. Jira Cloud).
   """
   logger.info(f"[Spillover] get_sprint_report_spillover: board_id={board_id} sprint_id={sprint_id}")
   base = JIRA_SERVER.rstrip("/")
   url = f"{base}/rest/agile/1.0/board/{board_id}/sprint/{sprint_id}/report"
   logger.info(f"[Spillover] get_sprint_report_spillover: url={url}")
   if not JIRA_USER_NAME or not JIRA_TOKEN:
       logger.info(f"[Spillover] get_sprint_report_spillover: no auth, returning [], False")
       return [], False
   auth: tuple[str, str] = (JIRA_USER_NAME, JIRA_TOKEN)
   try:
       r = requests.get(url, auth=auth, timeout=30)
       logger.info(f"[Spillover] get_sprint_report_spillover: response status={r.status_code} for board_id={board_id} sprint_id={sprint_id}")
       if r.status_code == 404:
           logger.info(f"[Spillover] get_sprint_report_spillover: 404 -> will use JQL fallback, returning [], True")
           return [], True
       if r.status_code != 200:
           logger.warning(f"[Spillover] get_sprint_report_spillover: non-200 -> returning [], False")
           return [], False
       data = r.json()
       logger.info(f"[Spillover] get_sprint_report_spillover: response keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
       # Try common response shapes (Cloud vs Server/DC may differ)
       issues_raw = None
       if "contents" in data:
           c = data["contents"]
           issues_raw = c.get("issuesNotCompletedInCurrentSprint") or c.get("issueNotCompletedInCurrentSprint")
           logger.info(f"[Spillover] get_sprint_report_spillover: from data['contents'] -> issues_raw type={type(issues_raw).__name__}")
       if issues_raw is None:
           issues_raw = data.get("issuesNotCompletedInCurrentSprint") or data.get("issueNotCompletedInCurrentSprint")
           logger.info(f"[Spillover] get_sprint_report_spillover: from data top-level -> issues_raw type={type(issues_raw).__name__}")
       if not issues_raw:
           logger.info(f"[Spillover] get_sprint_report_spillover: no issues_raw (empty or missing), returning [], False")
           return [], False
       # Normalize: can be list of issues or dict with "issues" key
       if isinstance(issues_raw, dict) and "issues" in issues_raw:
           issues_list = issues_raw["issues"]
           logger.info(f"[Spillover] get_sprint_report_spillover: issues_raw is dict with 'issues' -> issues_list len={len(issues_list)}")
       elif isinstance(issues_raw, list):
           issues_list = issues_raw
           logger.info(f"[Spillover] get_sprint_report_spillover: issues_raw is list -> issues_list len={len(issues_list)}")
       else:
           logger.info(f"[Spillover] get_sprint_report_spillover: issues_raw type not handled: {type(issues_raw).__name__}, returning [], False")
           return [], False
       result = []
       for item in issues_list:
           if isinstance(item, dict):
               key = item.get("key") or (item.get("id") and str(item.get("id")))
               fields = item.get("fields") or item
               summary = fields.get("summary") if isinstance(fields, dict) else None
               result.append({"key": key, "summary": summary or ""})
           else:
               result.append({"key": str(item), "summary": ""})
       logger.info(f"[Spillover] get_sprint_report_spillover: parsed result count={len(result)}, returning try_jql_fallback=False")
       return result, False
   except requests.exceptions.RequestException as e:
       logger.warning(f"[Spillover] get_sprint_report_spillover request failed: {e}")
       return [], False
   except (ValueError, KeyError) as e:
       logger.warning(f"[Spillover] get_sprint_report_spillover parse error: {e}")
       return [], False


def get_spillover_via_jql(sprint_id: int) -> list[dict]:
   """Fallback: get spillover (issues not completed in sprint) via JQL. Use when Sprint Report API returns 404 (e.g. Jira Cloud)."""
   logger.info(f"[Spillover] get_spillover_via_jql: sprint_id={sprint_id}")
   try:
       jql = f'sprint = {sprint_id} AND resolution is empty AND issuetype NOT IN subTaskIssueTypes() ORDER BY status ASC'
       logger.info(f"[Spillover] get_spillover_via_jql: JQL={jql}")
       issues = jira_client.search_issues(jql, maxResults=200, fields=["key", "summary"])
       result = [{"key": i.key, "summary": (i.fields.summary or "")} for i in issues]
       logger.info(f"[Spillover] get_spillover_via_jql: sprint_id={sprint_id} -> found {len(result)} issues (keys={[x['key'] for x in result[:5]]}{'...' if len(result) > 5 else ''})")
       return result
   except Exception as e:
       logger.warning(f"[Spillover] get_spillover_via_jql failed for sprint_id={sprint_id}: {e}")
       return []


def validate_sprint_name_format(sprint_name: str) -> tuple[bool, str]:
   """Validate sprint naming convention: PI<X> S<X> Squad_Name"""
   if not sprint_name:
       return False, "Sprint name is empty"

   pattern = r"^PI\d+ S\d+ .+$"
   if re.match(pattern, sprint_name):
       return True, ""

   example = "PI1 S1 Ecom Exp"
   return False, f"Expected format: PI<X> S<X> Squad_Name (e.g., {example})"


def escape_markdown_table_cell(text: str, max_length: int = 100) -> str:
   """Escape and clean text for markdown table cell."""
   if not text:
       return ""
   text = str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
   text = " ".join(text.split())
   return text[:max_length] + "..." if len(text) > max_length else text


def get_team_members_from_sprint(sprint_name: str, board_id: int) -> list[str]:
   """Auto-detect team members from active sprint only (assignees of issues in the current sprint). Excludes backlog."""
   try:
       jql = f'sprint = "{sprint_name}"'
       if JIRA_CUSTOM_JQL_FILTER:
           jql += f' AND {JIRA_CUSTOM_JQL_FILTER}'

       issues = jira_client.search_issues(jql, maxResults=500)
       team_members = {issue.fields.assignee.displayName for issue in issues
                       if issue.fields.assignee and issue.fields.assignee.displayName}
       members_list = sorted(team_members)
       logger.info(f"Auto-detected {len(members_list)} team members from active sprint: {members_list}")
       return members_list
   except Exception as e:
       logger.error(f"Failed to auto-detect team members from sprint: {e}")
       return []


def fetch_issues_for_team(team_name, team_config, sprint_name):
   """Fetch Jira issues for a specific team and check verification compliance"""
   board_id = team_config["board_id"]

   # Auto-detect team members from sprint if not explicitly provided
   if "members" in team_config and team_config["members"]:
       team_members = team_config["members"]
       logger.info(f"Using configured team members for {team_name}: {team_members}")
       # Build JQL query for specific team members
       members_jql = " OR ".join([f'assignee = "{member}"' for member in team_members])
       jql = f'sprint = "{sprint_name}" AND ({members_jql})'
   else:
       # Auto-detect: fetch all sprint issues; team list for header is resolved in generate_team_report
       jql = f'sprint = "{sprint_name}"'

   # Add custom filters if configured
   if JIRA_CUSTOM_JQL_FILTER:
       jql += f' AND {JIRA_CUSTOM_JQL_FILTER}'

   logger.info(f"Fetching issues for team {team_name} with JQL: {jql}")

   try:
       issues = jira_client.search_issues(jql, maxResults=100)
       sprint_features = []
       verification_violations = []
       stale_issues = []
       now = datetime.now().astimezone()

       logger.info(f"Found {len(issues)} issues for team {team_name}")

       for issue in issues:
           try:
               # Check verification compliance for this ticket
               verification_result = check_verification_checklist(jira_client, issue)
               compliance_icon = "✅" if verification_result["is_compliant"] else "⚠️"
               if not verification_result["is_compliant"]:
                   verification_violations.append(verification_result)

               comments = jira_client.comments(issue.key)
               comment_data = [
                   {
                       "comment": (comment.body or "")[:200],
                       "date": datetime.strptime(comment.updated, "%Y-%m-%dT%H:%M:%S.%f%z")
                   }
                   for comment in comments
               ]

               last_comment_time = max((c["date"] for c in comment_data), default=None)
               action_item = ""

               if not comments or (last_comment_time and (now - last_comment_time).days > 3):
                   assignee = issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned"
                   action_item = f"@{assignee} please provide a recent update."
                   stale_issues.append({
                       "key": issue.key,
                       "summary": issue.fields.summary,
                       "assignee": assignee
                   })

               recent_comment_summary = "<br>".join(
                   f"- {escape_markdown_table_cell(c['comment'][:150], 150)}" for c in comment_data[-2:]
               ) or "No comments"

               # Clean all fields for markdown table
               summary = escape_markdown_table_cell(issue.fields.summary, 100)
               status = escape_markdown_table_cell(issue.fields.status.name, 50)
               assignee = escape_markdown_table_cell(
                   issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned",
                   50
               )
               reporter = escape_markdown_table_cell(
                   issue.fields.reporter.displayName if issue.fields.reporter else "Unknown",
                   50
               )
               action_item = escape_markdown_table_cell(action_item, 100)

               sprint_features.append(
                   f"| {compliance_icon} [{issue.key}]({JIRA_SERVER}/browse/{issue.key}) | {summary} | {status} | "
                   f"{assignee} | {reporter} | {recent_comment_summary} | {action_item} |"
               )

           except Exception as e:
               logger.error(f"Error processing issue {issue.key}: {e}")
               continue
       return "\n".join(sprint_features), len(issues), verification_violations, stale_issues
   except Exception as e:
       logger.error(f"Failed to fetch issues for team {team_name}: {e}")
       return "", 0, [], []


def post_to_confluence(title: str, content: str) -> str | None:
   """Post a page to Confluence and return the page URL."""
   if not all([CONFLUENCE_BASE_URL, CONFLUENCE_USER, CONFLUENCE_TOKEN, CONFLUENCE_SPACE_KEY]):
       logger.warning("Confluence credentials not configured - skipping Confluence posting")
       return None

   # Validate space key format (warn if personal space)
   if CONFLUENCE_SPACE_KEY.startswith("~"):
       logger.warning(f"Using personal Confluence space ({CONFLUENCE_SPACE_KEY}). Consider using a team space key.")

   try:
       api_base = CONFLUENCE_BASE_URL.rstrip("/").replace("/wiki", "")
       api_url = f"{api_base}/wiki/rest/api/content"
       auth = (CONFLUENCE_USER, CONFLUENCE_TOKEN)
       headers = {"Content-Type": "application/json"}

       # Convert markdown to HTML for better rendering
       html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])

       payload = {
           "type": "page",
           "title": title,
           "space": {"key": CONFLUENCE_SPACE_KEY},
           "body": {
               "storage": {
                   "value": html_content,
                   "representation": "storage"
               }
           }
       }

       response = requests.post(api_url, json=payload, auth=auth, headers=headers, timeout=30)
       if response.status_code == 200:
           try:
               page_id = response.json()["id"]
               wiki_base = CONFLUENCE_BASE_URL.rstrip("/")
               page_url = f"{wiki_base}/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
               logger.info(f"Successfully posted to Confluence: {page_url}")
               return page_url
           except (KeyError, ValueError) as e:
               logger.error(f"Failed to parse Confluence response: {e}")
               return None

       # Duplicate title: find existing page and update it (version history preserved in Confluence)
       if response.status_code == 400:
           try:
               err = response.json()
               err_str = str(err).lower()
               if "already exists" in err_str or "same title" in err_str:
                   search_url = f"{api_url}?type=page&spaceKey={quote(CONFLUENCE_SPACE_KEY)}&title={quote(title)}&expand=version"
                   search_resp = requests.get(search_url, auth=auth, headers=headers, timeout=30)
                   if search_resp.status_code != 200:
                       logger.error(f"Confluence search for existing page failed: {search_resp.status_code}")
                       return None
                   data = search_resp.json()
                   results = data.get("results") or []
                   if not results:
                       logger.error("Confluence: duplicate title reported but no page found by title")
                       return None
                   existing = results[0]
                   page_id = existing["id"]
                   version = existing.get("version", {})
                   current_version = version.get("number")
                   if current_version is None:
                       get_page = requests.get(f"{api_url}/{page_id}?expand=version", auth=auth, headers=headers, timeout=30)
                       if get_page.status_code == 200:
                           current_version = get_page.json().get("version", {}).get("number", 1)
                       else:
                           current_version = 1
                   current_version = int(current_version)
                   update_payload = {
                       "id": page_id,
                       "type": "page",
                       "title": title,
                       "space": {"key": CONFLUENCE_SPACE_KEY},
                       "body": {"storage": {"value": html_content, "representation": "storage"}},
                       "version": {"number": current_version + 1},
                   }
                   update_url = f"{api_url}/{page_id}"
                   update_resp = requests.put(update_url, json=update_payload, auth=auth, headers=headers, timeout=30)
                   if update_resp.status_code == 200:
                       wiki_base = CONFLUENCE_BASE_URL.rstrip("/")
                       page_url = f"{wiki_base}/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
                       logger.info(f"Updated existing Confluence page: {page_url}")
                       return page_url
                   logger.error(f"Confluence update failed: {update_resp.status_code} - {update_resp.text[:200]}")
           except (KeyError, ValueError, requests.exceptions.RequestException) as e:
               logger.error(f"Confluence update flow failed: {e}")

       error_msg = f"Status {response.status_code}"
       try:
           if response.content:
               error_detail = response.json()
               error_msg += f" - {error_detail}"
       except Exception:
           error_msg += f" - {response.text[:200]}"
       logger.error(f"Failed to post to Confluence: {error_msg}")
       return None
   except requests.exceptions.RequestException as e:
       logger.error(f"Network error posting to Confluence: {e}")
       return None
   except Exception as e:
       logger.error(f"Error posting to Confluence: {e}", exc_info=True)
       return None


TEAMS_MESSAGE_MAX_LEN = 28_000


def post_to_teams(webhook_url: str, message: str, recipient_name: str):
   """Send message to Microsoft Teams via webhook."""
   if not webhook_url:
       logger.warning(f"No webhook URL provided for {recipient_name}")
       return False

   try:
       if len(message) > TEAMS_MESSAGE_MAX_LEN:
           message = message[:TEAMS_MESSAGE_MAX_LEN] + "\n\n... [Report truncated due to size limit]"
           logger.warning(f"Report truncated for {recipient_name} due to size limit")

       response = requests.post(webhook_url, json={"text": message}, timeout=30)
       if response.status_code == 200:
           logger.info(f"Successfully posted to Teams for {recipient_name}")
           return True
       else:
           logger.error(f"Failed to post to Teams for {recipient_name}: {response.status_code}")
           return False
   except Exception as e:
       logger.error(f"Error posting to Teams for {recipient_name}: {e}")
       return False


def generate_team_report(team_name, team_config):
   """Generate report for a specific team and collect verification violations"""
   logger.info(f"Generating report for team: {team_name}")

   try:
       sprint_name = get_active_sprint_name(team_config["board_id"])

       # Optional: exclude specific sprint names; when active is excluded, try an allowed sprint (e.g. PI2 S3 Dig Exp)
       excluded = team_config.get("excluded_sprint_names") or []
       included = team_config.get("included_sprint_names")
       if sprint_name and excluded and sprint_name.strip().lower() in [s.strip().lower() for s in excluded]:
           sprint_name = get_allowed_sprint_name(
               team_config["board_id"],
               excluded_sprint_names=excluded,
               included_sprint_names=included,
           )
           if not sprint_name:
               logger.info(f"Skipping team {team_name}: active sprint was excluded and no allowed sprint found")
               return None, f"⏭️ No allowed sprint for this team (excluded sprints only).", "", []

       if not sprint_name:
           logger.warning(f"No active sprint found for team {team_name}")
           return None, f"⚠️ No active sprint found for team {team_name}", "", []

       logger.info(f"Active sprint for {team_name}: {sprint_name}")

       sprint_name_valid, sprint_name_message = validate_sprint_name_format(sprint_name)
       naming_warning = ""
       if not sprint_name_valid:
           naming_warning = f"⚠️ Sprint name '{sprint_name}' does not match PI<X> S<X> Squad_Name. {sprint_name_message}"
           logger.warning(naming_warning)
       issues_markdown, issue_count, verification_violations, stale_issues = fetch_issues_for_team(team_name, team_config, sprint_name)

       if not issues_markdown:
           logger.warning(f"No issues found for team {team_name}")
           return None, f"ℹ️ No issues found for team {team_name} in sprint {sprint_name}", "", []

       logger.info(f"Generating AI summary for team {team_name} with {issue_count} issues")

       # Build stale issues text
       stale_text = "\n".join([f"- **{s['key']}**: {s['summary']} (Assignee: {s['assignee']})" for s in stale_issues]) if stale_issues else "No stale issues identified."

       result = sprint_chain.invoke({
           "team_name": team_name,
           "sprint_name": sprint_name,
           "sprint_features": issues_markdown,
           "stale_issues": stale_text
       })

       report = result.content if hasattr(result, "content") else str(result)

       # Get team members for the report header (active sprint only when auto-detect; no backlog)
       if "members" in team_config and team_config["members"]:
           detected_members = team_config["members"]
       else:
           detected_members = get_team_members_from_sprint(sprint_name, team_config["board_id"])

       # Add header with clean table format
       header = f"# 📊 Daily Jira Status Report - {team_name}\n\n"
       header += "| Sprint | Details |\n"
       header += "|--------|---------|\n"
       header += f"| **Sprint** | {sprint_name} |\n"
       if naming_warning:
           header += f"| **⚠️ Warning** | {naming_warning} |\n"
       header += f"| **Date** | {datetime.now().strftime('%B %d, %Y')} |\n"
       header += f"| **Total Issues** | {issue_count} |\n"
       header += f"| **Team Members** | {', '.join(detected_members)} |\n\n"

       # Spillover from previous sprint (Sprint Report API, or JQL fallback on Jira Cloud)
       board_id = team_config["board_id"]
       logger.info(f"[Spillover] team={team_name} board_id={board_id}: fetching last closed sprint id")
       last_closed_sprint_id = get_last_closed_sprint_id(board_id)
       if last_closed_sprint_id:
           logger.info(f"[Spillover] team={team_name}: last_closed_sprint_id={last_closed_sprint_id}, calling get_sprint_report_spillover")
           spillover, try_jql_fallback = get_sprint_report_spillover(board_id, last_closed_sprint_id)
           logger.info(f"[Spillover] team={team_name}: get_sprint_report_spillover returned spillover count={len(spillover)} try_jql_fallback={try_jql_fallback}")
           if try_jql_fallback:
               logger.info(f"[Spillover] team={team_name}: using JQL fallback for sprint_id={last_closed_sprint_id}")
               spillover = get_spillover_via_jql(last_closed_sprint_id)
               logger.info(f"[Spillover] team={team_name}: after JQL fallback spillover count={len(spillover)}")
           if spillover:
               logger.info(f"[Spillover] team={team_name}: adding spillover section with {len(spillover)} issues to report")
               header += "### Spillover from previous sprint\n\n"
               header += "Issues not completed in the last closed sprint (carried over):\n\n"
               header += "| Issue | Summary |\n"
               header += "|-------|--------|\n"
               for s in spillover:
                   key = s.get("key") or "—"
                   summary = escape_markdown_table_cell(s.get("summary") or "", 80)
                   header += f"| [{key}]({JIRA_SERVER}/browse/{key}) | {summary} |\n"
               header += "\n"
           else:
               logger.info(f"[Spillover] team={team_name}: no spillover issues (empty list), adding 'No spillover' message to report")
               header += "### Spillover from previous sprint\n\nNo spillover issues from the last closed sprint.\n\n"
       else:
           logger.info(f"[Spillover] team={team_name}: no last_closed_sprint_id, skipping spillover section")
       header += "---\n\n"
       full_report = header + report
       return full_report, None, naming_warning, verification_violations
   except Exception as e:
       error_msg = f"Error generating report for team {team_name}: {e}"
       logger.error(error_msg)
       return None, error_msg, "", []


@functions_framework.http
def generate_and_send_report(request):
   """Generate team reports, post one Confluence page (team-wise sections), and send the link once to the shared Teams channel."""
   logger.info("Starting Daily Jira Report Generation")

   results = {
       "timestamp": datetime.now().isoformat(),
       "teams": {},
       "scrum_master": {"sent": False, "message": ""}
   }

   all_team_reports = []

   try:
       for idx, (team_name, team_config) in enumerate(TEAMS_CONFIG.items()):
           logger.info(f"\n{'=' * 80}")
           logger.info(f"Processing team: {team_name}")
           logger.info(f"{'=' * 80}")

           report, error, naming_warning, verification_violations = generate_team_report(team_name, team_config)

           # Add delay between teams to avoid rate limits (except after last team)
           if idx < len(TEAMS_CONFIG) - 1:
               logger.info("Waiting 15 seconds before processing next team to avoid rate limits...")
               time.sleep(15)

           if error:
               results["teams"][team_name] = {
                   "status": "error",
                   "message": error,
                   "sent_to_em": False,
                   "naming_warning": naming_warning,
                   "verification_violations": verification_violations,
               }
               continue

           all_team_reports.append({
               "team_name": team_name,
               "report": report,
               "naming_warning": naming_warning,
               "verification_violations": verification_violations,
           })
           results["teams"][team_name] = {
               "status": "success",
               "message": "",
               "sent_to_em": False,
               "naming_warning": naming_warning,
               "verification_violations": verification_violations,
           }

       if all_team_reports:
           logger.info("Building single Confluence page (all teams, team-wise sections)")

           consolidated_report = "# 📊 Daily Jira Status Report - All Teams\n\n"
           consolidated_report += f"**Date:** {datetime.now().strftime('%B %d, %Y')}\n"
           consolidated_report += f"**Teams Included:** {', '.join([t['team_name'] for t in all_team_reports])}\n\n"

           naming_warnings_list = [t['naming_warning'] for t in all_team_reports if t.get('naming_warning')]
           if naming_warnings_list:
               consolidated_report += "**Naming Alerts:**\n"
               for w in naming_warnings_list:
                   consolidated_report += f"- {w}\n"
               consolidated_report += "\n"

           all_violations = []
           for team_data in all_team_reports:
               if team_data.get('verification_violations'):
                   all_violations.extend(team_data['verification_violations'])
           if all_violations:
               consolidated_report += "**Jira Verification Violations:**\n"
               for v in all_violations:
                   consolidated_report += f"- **{v['issue_key']}** ({v['status']}): {', '.join(v['violations'])}\n"
               consolidated_report += "\n"

           consolidated_report += "---\n\n"

           for team_data in all_team_reports:
               consolidated_report += f"## Team: {team_data['team_name']}\n\n"
               # Include full report (header with Sprint, Team Members, Spillover, then AI summary and table)
               consolidated_report += team_data['report']
               consolidated_report += "\n\n" + "=" * 80 + "\n\n"

           confluence_title = f"Daily Jira Report - All Teams - {datetime.now().strftime('%B %d, %Y')}"
           confluence_url = post_to_confluence(confluence_title, consolidated_report)

           # Teams: title + date, Confluence link, teams list, and Error section if any.
           now = datetime.now(ZoneInfo("Asia/Kolkata"))
           time_12h = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'} IST"
           date_str = f"{now.strftime('%B %d, %Y')} {time_12h}"
           teams_included = ", ".join([t["team_name"] for t in all_team_reports])
           if confluence_url:
               teams_message = (
                   f"📊 **Daily Jira Status Report - All Teams**\n\n"
                   f"**Date:** {date_str}\n\n"
                   f"🔗 [Open in Confluence]({confluence_url})\n\n"
                   f"**Teams Included:** {teams_included}\n"
               )
               result_note = "Report link"
           else:
               teams_message = (
                   f"📊 **Daily Jira Status Report - All Teams**\n\n"
                   f"**Date:** {date_str}\n\n"
                   f"Confluence page could not be created (duplicate title or error). Please check Confluence or try again later.\n\n"
                   f"**Teams Included:** {teams_included}\n"
               )
               result_note = "Report link not created (Confluence error)"

           error_entries = [f"- {name}: {data['message']}" for name, data in results["teams"].items() if data.get("status") == "error"]
           if error_entries:
               teams_message += "\n**Error:**\n" + "\n".join(error_entries)

           channel_sent = post_to_teams(REPORT_CHANNEL_WEBHOOK, teams_message, "Report channel") if REPORT_CHANNEL_WEBHOOK else False
           msg_suffix = f" {'sent' if channel_sent else 'not sent'} to shared channel"
           for team_data in all_team_reports:
               results["teams"][team_data["team_name"]]["sent_to_em"] = channel_sent
               results["teams"][team_data["team_name"]]["message"] = result_note + msg_suffix
           results["scrum_master"] = {
               "sent": channel_sent,
               "message": (result_note + msg_suffix) if REPORT_CHANNEL_WEBHOOK else "No report channel webhook configured",
               "teams_included": len(all_team_reports),
           }
       else:
           results["scrum_master"]["message"] = "No team reports available to consolidate" if REPORT_CHANNEL_WEBHOOK else "No webhook configured"
           for team_name in results["teams"]:
               if results["teams"][team_name].get("message") == "":
                   results["teams"][team_name]["message"] = "No report generated (error or no issues)"

       success_count = sum(1 for t in results["teams"].values() if t.get("sent_to_em"))
       total_teams = len(TEAMS_CONFIG)
       summary = f"\n✅ Report link sent to shared channel: {'Yes' if results['scrum_master']['sent'] else 'No'}\n"
       summary += f"✅ Teams included: {success_count}/{total_teams}\n"
       for team_name, team_result in results["teams"].items():
           summary += f"  • {team_name}: {team_result['message']}\n"

       logger.info("Report summary: %s", summary.replace("\n", " "))

       overall_success = success_count == total_teams and (results["scrum_master"]["sent"] or not REPORT_CHANNEL_WEBHOOK)
       return (summary, 200 if overall_success else 207)

   except Exception as e:
       error_msg = f"❌ Critical error in report generation: {str(e)}"
       logger.error(error_msg, exc_info=True)
       return (error_msg, 500)


if __name__ == "__main__":
   class MockRequest:
       """Mock request for local testing."""
       def __init__(self) -> None:
           self.args: dict[str, Any] = {}
           self.headers: dict[str, str] = {}
           self.method: str = "GET"

   logger.info("Running in local test mode")
   result, status = generate_and_send_report(MockRequest()) # type: ignore
   print(f"\nResult ({status}):\n{result}")
