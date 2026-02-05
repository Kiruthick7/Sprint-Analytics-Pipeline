# Daily Jira Report Generator

Automatically generates daily Jira sprint reports for multiple teams, posts a consolidated report to Confluence, and sends a link to a shared Microsoft Teams channel. Uses an LLM (Groq or Gemini) for summaries and includes spillover from the previous sprint.

## Features

- **Multi-team**: Processes each team from `team_config.py` (Jira board ID per team).
- **Single Confluence page**: One page per run with team-wise sections (Sprint info, Team members, **Spillover from previous sprint**, AI summary, issues table).
- **Teams notification**: One message to the shared channel with Confluence link, date (IST), and teams included.
- **Spillover**: Shows issues not completed in the last closed sprint (JQL fallback on Jira Cloud when Sprint Report API returns 404).
- **Team members**: Auto-detected from current sprint + board backlog; optional explicit list in config.
- **Verification checklist**: Per-issue compliance (AC comment, PR link, labels, evidence) via `verification_helpers.py`.
- **LLM summary**: Groq (llama-4-scout-17b) or Gemini (gemini-1.5-flash) for progress, blockers, stale issues, action items.

## Prerequisites

- Python 3.11+
- Jira (Atlassian) account with API token
- Confluence space (for report page)
- Microsoft Teams incoming webhook (for the shared channel)
- Groq API key (default) or Google AI / Gemini API key (optional)

## Quick Start

### 1. Clone and install

```bash
cd /path/to/Jira
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env: Jira, Confluence, Teams webhook, LLM provider and API key
```

Required in `.env`:

| Variable | Description |
|----------|-------------|
| `JIRA_USER_NAME` | Jira user email |
| `JIRA_TOKEN` | Jira API token |
| `TEAMS_WEBHOOK` | Teams incoming webhook URL for report channel |
| `CONFLUENCE_BASE_URL` | e.g. `https://your-site.atlassian.net/wiki` |
| `CONFLUENCE_USER` | Confluence user email |
| `CONFLUENCE_TOKEN` | Confluence API token |
| `CONFLUENCE_SPACE_KEY` | Target space key (e.g. `HGD`) |
| `LLM_PROVIDER` | `groq` (default) or `gemini` |
| `GROQ_API_KEY` | Required if `LLM_PROVIDER=groq` |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Required if `LLM_PROVIDER=gemini` |

### 3. Configure teams

Edit `team_config.py`: set `board_id` (and optionally `members`) for each team. Use `members: []` to auto-detect from sprint + backlog.

### 4. Run locally

```bash
python main.py
```

This runs the full flow: fetch sprint/issues per team, generate AI summary, post one Confluence page, and send the link to Teams.

## Project Structure

```
.
├── main.py                 # Entrypoint: report generation, Confluence, Teams
├── team_config.py          # Teams, board IDs, optional members, JQL filter
├── verification_helpers.py  # Jira verification checklist (AC, PR, labels, etc.)
├── requirements.txt
├── .env.example
├── deploy.sh               # Deploy to Google Cloud Function
├── Dockerfile              # Container build for Cloud Run / GCP
├── main.tf                 # Terraform (optional)
└── README.md
```

## Deployment (Google Cloud)

1. Set in `.env`: `GCP_PROJECT_ID`, `GCP_REGION`, `CLOUD_FUNCTION_NAME`, `SERVICE_ACCOUNT_EMAIL`.
2. Run:

```bash
./deploy.sh 
```

This deploys the Cloud Function and sets up Cloud Scheduler. Adjust the schedule in `deploy.sh` if needed (e.g. 9 AM on weekdays).

## Configuration Details

- **JQL filter**: Default in `team_config.py` excludes resolved/canceled, empty assignee, and sub-tasks. Override with `JIRA_CUSTOM_JQL_FILTER` if needed.
- **Sprint naming**: Report warns if sprint name doesn’t match `PI<X> S<X> Squad_Name`; it does not change behavior.
- **Rate limiting**: 15-second delay between teams to avoid Jira/API rate limits.

## License

Internal use. Adjust `team_config.py` and `.env` for your org.
