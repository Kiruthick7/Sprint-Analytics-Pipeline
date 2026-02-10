"""Environment and shared client configuration."""
import os
import logging
from dotenv import load_dotenv
from jira import JIRA

load_dotenv()

logger = logging.getLogger(__name__)

# Jira
JIRA_SERVER = os.getenv("JIRA_SERVER", "https://hy-vee.atlassian.net")
JIRA_USER_NAME = os.getenv("JIRA_USER_NAME")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

if not JIRA_USER_NAME or not JIRA_TOKEN:
    logger.error("Missing environment variables: JIRA_USER_NAME or JIRA_TOKEN")
    raise ValueError("Missing environment variables: JIRA_USER_NAME and JIRA_TOKEN are required")

try:
    jira_client = JIRA(server=JIRA_SERVER, basic_auth=(JIRA_USER_NAME, JIRA_TOKEN))
except Exception as e:
    logger.error(f"Failed to initialize Jira client: {e}")
    raise

# Story points: Jira Cloud often uses customfield_10016; set JIRA_STORY_POINTS_FIELD if different
JIRA_STORY_POINTS_FIELD = os.getenv("JIRA_STORY_POINTS_FIELD", "customfield_10016")

# Confluence
CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "https://hy-vee.atlassian.net/wiki")
CONFLUENCE_USER = os.getenv("CONFLUENCE_USER", "")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_TOKEN", "")
CONFLUENCE_SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "")

# Public URL of this app (for links on Confluence report to Individual Person lookup page)
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

# Optional: link under "Velocity" heading on report (replaces inline velocity table)
VELOCITY_DASHBOARD_URL = os.getenv("VELOCITY_DASHBOARD_URL", "").strip()
