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
        "excluded_sprint_names": ["Dig Exp | Azkaban", "S4 | GS Phase 1"],
        # * matches any sprint number (e.g. PI2 S3, PI2 S4, PI2 S5 Dig Exp) so no config change each sprint
        "included_sprint_names": ["PI2 S* Dig Exp"],
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

# Custom JQL filter (optional) – used for team reports (excludes subtasks)
JIRA_CUSTOM_JQL_FILTER = (
    "status NOT IN (RESOLVED, CANCELED) "
    "AND assignee IS NOT EMPTY "
    "AND issuetype NOT IN subTaskIssueTypes() "
    "ORDER BY status ASC"
)

# Person Lookup: include subtasks so assignee sees all their tickets (stories + subtasks)
JIRA_PERSON_LOOKUP_JQL_FILTER = (
    "status NOT IN CANCELED "
    "AND assignee IS NOT EMPTY "
    "ORDER BY status ASC"
)
