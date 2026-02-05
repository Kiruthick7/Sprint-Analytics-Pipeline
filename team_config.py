"""Team configuration for the Daily Jira Report Generator."""

import os
from dotenv import load_dotenv

load_dotenv()

# Teams in Digital Grocery Squad
TEAMS_CONFIG = {
    "Ecom Expansion": {
        "board_id": 1413,
        "em_name": "ARUN",
        "members": []
    },
    "Pharmacy": {
        "board_id": 279,
        "em_name": "ROHIT",
        "members": []
    },
    "Dig Experience": {
        "board_id": 385,
        "em_name": "SRINIDHI",
        "members": [],
        # When active sprint is in excluded list, use first matching included sprint instead of skipping
        "excluded_sprint_names": ["Dig Exp | Azkaban", "S4 | GS Phase 1"],
        "included_sprint_names": ["PI2 S3 Dig Exp"],
    },
    "Redmedia Global": {
        "board_id": 1907,
        "em_name": "ROJALIN",
        "members": []
    },
    "RedBox Rx": {
        "board_id": 376,
        "em_name": "ROHIT",
        "members": []
    },
}

# Single channel where all EMs and Scrum Master are; one post with Confluence link
REPORT_CHANNEL_WEBHOOK = os.getenv("TEAMS_WEBHOOK")

# Custom JQL filter (optional)
JIRA_CUSTOM_JQL_FILTER = (
    "status NOT IN (RESOLVED, CANCELED) "
    "AND assignee IS NOT EMPTY "
    "AND issuetype NOT IN subTaskIssueTypes() "
    "ORDER BY status ASC"
)
