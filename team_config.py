"""Team configuration for the Daily Jira Report Generator."""

import os
from dotenv import load_dotenv

load_dotenv()

# Teams in display order: Dig Experience, Ecom Expansion, Redmedia Global, Pharmacy
TEAMS_CONFIG = {
    "Dig Experience": {
        "board_id": 385,
        "em_name": "SRINIDHI",
        "members": [],
        "excluded_sprint_names": ["Dig Exp | Azkaban", "S4 | GS Phase 1"],
        "included_sprint_names": ["PI* S* Dig Exp"],
    },
    "Ecom Expansion": {
        "board_id": 1413,
        "em_name": "ARUN",
        "members": [],
    },
    "Redmedia Global": {
        "board_id": 1907,
        "em_name": "ROJALIN",
        "members": [],
    },
    "Pharmacy": {
        "board_id": 279,
        "em_name": "ROHIT",
        "members": [],
    },
    # "RedBox Rx": {
    #     "board_id": 376,
    #     "em_name": "ROHIT",
    #     "members": []
    # },
}

# Single channel where all EMs and Scrum Master are; one post with Confluence link
REPORT_CHANNEL_WEBHOOK = os.getenv("TEAMS_WEBHOOK")

# Only Story and Bug (exclude Task, Epic, Sub-task, etc.) for report and lookup
JIRA_REPORT_ISSUE_TYPES_JQL = 'issuetype IN ("Story", "Bug")'

# Custom JQL filter (optional) – used for team reports (Story + Bug only)
JIRA_CUSTOM_JQL_FILTER = (
    "status NOT IN (RESOLVED, CANCELED) "
    "AND assignee IS NOT EMPTY "
    f"AND {JIRA_REPORT_ISSUE_TYPES_JQL} "
    "ORDER BY status ASC"
)

# Person Lookup: Story + Bug only (same as report)
JIRA_PERSON_LOOKUP_JQL_FILTER = (
    "status NOT IN CANCELED "
    "AND assignee IS NOT EMPTY "
    f"AND {JIRA_REPORT_ISSUE_TYPES_JQL} "
    "ORDER BY status ASC"
)
