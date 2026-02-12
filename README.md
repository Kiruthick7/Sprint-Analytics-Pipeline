# HSTG Digital Jira Report Generator

Generates Jira sprint reports for multiple teams, posts one consolidated page to Confluence, and notifies a Microsoft Teams channel. Includes **Developer lookup**: open the report and use the link (or embedded iframe) to look up a developer and see their story points and issues.

[![Jira Sprint Report](https://github.com/Kiruthick7/Jira_Status_Report_Tool/actions/workflows/jira-report.yml/badge.svg?event=workflow_dispatch)](https://github.com/Kiruthick7/Jira_Status_Report_Tool/actions/workflows/jira-report.yml)

## Features

- **Multi-team reports** from `team_config.py` (board ID per team, optional members / excluded sprints)
- **Single Confluence page** — the same page is **updated** each run; sprint details, spillover, verification violations, AI summary (Groq or Gemini)
- **Sprint details table** — Sprint name, Actual Start/End, Completed story points, Issues in Sprint, Issues in backlog, **Issues with no story points**, Total Story Points
- **Spillover** — Issues not completed in the last closed sprint (table: Issue, Summary, **Assignee**, **Label** e.g. tech-debt)
- **Blockers section (AI)** — **Issues on Backlog** and **Issues on Hold** as separate subsections with one-line summaries
- **Jira Verification Violations** — Doughnut chart by type + **Per-issue details** table (Issue, Status, **Assignee**, Violations). Rules are status-dependent: In Progress → +1 AC only; In Test → +1 AC + PR link; Ready for Production / Resolved → all 7 (PR link, branch cleanup, Concourse, rollback, evidence labels, time logged)
- **Story point breakdown** (Developer lookup) — Not started, In progress, Completed, Blocked/On hold (board column or status name)
- **Teams notification** with Confluence link (date in IST)
- **Developer lookup** — Link and optional iframe on Confluence; type name, pick team/sprint, fetch to see story points and issue list
- **Cloud Scheduler** — Run the report on a schedule (default: Monday & Thursday 9 AM IST; configurable)

## Prerequisites

- Python 3.11+
- Jira account + API token
- Confluence space (for the report page)
- Microsoft Teams incoming webhook (for the report link)
- Groq or Gemini API key (for AI summary)

---

## How to run

**📋 [Step-by-step guide: all ways to run the report + Developer lookup](docs/RUNNING_THE_REPORT.md)** — Run the report (once, web app, GCP, cron, GitHub Actions, HTTP trigger) and how to use the HTML Developer lookup page (enter name, team, sprint → fetch from Jira).

**🚀 [GitHub Actions + host Developer lookup (Render or Railway)](docs/GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md)** — Schedule the report with GitHub Actions (Mon & Thu 9 AM IST) and host the Developer lookup at a public URL. Use Render (free or paid) or Railway (Hobby ~$5/mo for always-on). For Render deploy steps, see [RENDER_DEPLOY_CHECKLIST.md](docs/RENDER_DEPLOY_CHECKLIST.md).

### Step 1: Clone and install

```bash
cd /path/to/Jira
pip install -r requirements.txt
```

### Step 2: Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at least:

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_USER_NAME` | Yes | Jira user email |
| `JIRA_TOKEN` | Yes | Jira API token |
| `CONFLUENCE_BASE_URL` | For report | e.g. `https://your-site.atlassian.net/wiki` |
| `CONFLUENCE_USER` | For report | Confluence user email |
| `CONFLUENCE_TOKEN` | For report | Confluence API token |
| `CONFLUENCE_SPACE_KEY` | For report | Space key (e.g. `HGD`) |
| `VELOCITY_DASHBOARD_URL` | Optional | Link under the Velocity heading (replaces inline table) |
| `TEAMS_WEBHOOK` | For report | Teams incoming webhook URL |
| `LLM_PROVIDER` | For report | `groq` or `gemini` |
| `GROQ_API_KEY` | If groq | Groq API key |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | If gemini | Gemini/Google AI key |
| `APP_BASE_URL` | For Person Lookup on Confluence | Public URL of this app (see Step 4 / Deploy) |

### Step 3: Configure teams

Edit `team_config.py`: set `board_id` for each team. Use `members: []` to auto-detect assignees from the sprint.

### Step 4: Run locally

**Option A – Generate the report once (no browser)**

```bash
python main.py
```

Runs the full flow (fetch from Jira, build report, post to Confluence, send link to Teams) and exits.

**Option B – Run the web app (report + Person Lookup in browser)**

```bash
functions_framework --target=generate_and_send_report --port=8080 --debug
```

Use `--debug` so the app runs in a single process (Flask dev server). That can avoid worker crashes (SIGSEGV) you may see when hitting `/report` with the default multi-worker setup. Keep the terminal running. Then:

| What you want | Open in browser |
|---------------|------------------|
| **Developer lookup** (name, team/sprint, fetch) | http://localhost:8080/ or http://localhost:8080/person |
| **Run the report manually** | http://localhost:8080/report |

For Developer lookup to appear on the Confluence page when deployed, set `APP_BASE_URL` in `.env` (e.g. `http://localhost:8080` locally, or your Cloud Function URL in production).

### Step 5: Deploy to Google Cloud (production)

1. In `.env`, set:
   - `GCP_PROJECT_ID`, `GCP_REGION`, `CLOUD_FUNCTION_NAME`
   - All vars from Step 2 (Jira, Confluence, Teams, LLM)
2. Run:

```bash
./deploy.sh
```

This will:

- Deploy the app as a **Cloud Function** (HTTP).
- Create a **Cloud Scheduler** job that calls the function at **Monday and Thursday 9:00 AM IST** at `/report` (generates the report and posts to Confluence + Teams).

3. **After the first deploy:** copy the function URL from the script output and set in `.env`:

   ```bash
   APP_BASE_URL=https://REGION-PROJECT.cloudfunctions.net/FUNCTION_NAME
   ```

   Then run `./deploy.sh` again so the function gets `APP_BASE_URL`. The Confluence report will then show the **Developer lookup** section (link and optional embedded iframe).

**Schedule:** Default is **Monday and Thursday 9:00 AM IST** (`0 9 * * 1,4`). To change it, set in `.env` before deploying:

```bash
SCHEDULE_CRON="0 9 * * 1,4"    # 1=Mon, 4=Thu (0=Sun ... 6=Sat)
SCHEDULE_TIMEZONE=Asia/Kolkata
```

**Alternative: Terraform**
Use `main.tf` and `terraform.tfvars` (copy from `terraform.tfvars.example`) to provision the Cloud Function and Scheduler. Set `teams_webhook`, Confluence vars, and LLM keys in `terraform.tfvars`. The app uses a single Teams webhook for the report link.

---

## Other ways to run the report on a schedule (no Cloud Function + Scheduler)

If you prefer not to use GCP Cloud Function + Cloud Scheduler, you can run the report on **Mon & Thu 9 AM IST** with one of these:

| Option | Best for | Notes |
|--------|----------|--------|
| **Cron on a server** | You have a Linux/macOS VM or always-on machine | Run `python main.py` or `curl http://your-app/report` at 9 AM IST Mon & Thu. No cloud scheduler needed. |
| **GitHub Actions** | Repo is on GitHub; you’re okay running in CI | Scheduled workflow runs at 9 AM IST (e.g. `30 3 * * 1,4` in UTC = 03:30 UTC Mon & Thu). Store secrets in repo secrets and run `python main.py` in the workflow. |
| **Cloud Run Job + Scheduler** | You want to stay on GCP but not use a Function | Deploy the app as a **Cloud Run Job** (runs to completion). Use Cloud Scheduler to invoke the job at the same cron. |
| **Other clouds** | You already use AWS/Azure | **AWS:** Lambda + EventBridge schedule. **Azure:** Function App with timer trigger. You’d deploy the same Python app and call the report entrypoint on schedule. |

**Cron example (server / VM):**  
Use the same cron expression in the server’s timezone (e.g. if the server is in IST):

```bash
# crontab -e  (Mon & Thu 9:00 AM)
0 9 * * 1,4 cd /path/to/Jira && /usr/bin/python3 main.py
```

If the server is in UTC, 9 AM IST = 03:30 UTC:

```bash
30 3 * * 1,4 cd /path/to/Jira && /usr/bin/python3 main.py
```

**GitHub Actions:**  
A workflow is included at `.github/workflows/jira-report.yml` (Mon & Thu 03:30 UTC = 9 AM IST). Add **repository secrets**: `JIRA_USER_NAME`, `JIRA_TOKEN`, `TEAMS_WEBHOOK`, `CONFLUENCE_*`, `LLM_PROVIDER`, `GROQ_API_KEY` or `GEMINI_API_KEY`. For the **Developer lookup** and **Sprint metrics dashboard** links to appear in the Confluence report, also add `APP_BASE_URL` (your hosted app URL, no trailing slash) and optionally `VELOCITY_DASHBOARD_URL`. See [GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md](docs/GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md). Trigger the workflow manually from the Actions tab.

---

## End-to-end flow

1. **Report is generated**
   Either by Cloud Scheduler (Mon & Thu 9 AM IST) or by you (e.g. `python main.py` or http://localhost:8080/report). The app posts one Confluence page with all teams’ sections.

2. **You open the Confluence page**
   From the link in Teams or from your Confluence space. You see the full report: date, teams included, Developer lookup link, then per team — sprint details, naming alerts (if any), Jira Verification Violations (doughnut + per-issue table), spillover, and AI summary (Blockers: Issues on Backlog, Issues on Hold).

3. **Developer lookup**
   Scroll to the bottom of the Confluence page. Use **"Open Developer lookup in a new tab"** (recommended; Confluence often blocks iframes). Type the developer name, select team and sprint, click **Fetch from Jira** to see story points (Total, Not started, In progress, Completed, Blocked/On hold) and the issue list.

---

## Project structure

```
.
├── main.py                   # HTTP entrypoint: routing, report, Person Lookup API
├── config.py                 # Env and Jira/Confluence config
├── team_config.py            # Teams, board IDs, optional members
├── jira_api/
│   ├── jira_sprint.py        # Sprint info, spillover, velocity, story points (Agile API)
│   ├── jira_issues.py        # Issue fetch, team members, story points, backlog/on-hold, verification
│   └── verification_helpers.py  # Status-dependent checklist (+1 AC, PR link, evidence, etc.)
├── report/
│   ├── report_builder.py     # Build team reports and consolidated Confluence content
│   ├── violations_chart.py  # Verification violations: doughnut chart, stacked bar, summary table
│   └── llm_report.py        # LLM and prompt for AI summary
├── clients/
│   ├── confluence_client.py # Post/update Confluence page
│   └── teams_client.py       # Post to Teams webhook
├── person/
│   └── person_lookup.py      # Developer list, sprints, person details (story points by status)
├── utils/
│   └── markdown.py           # Markdown/table helpers (e.g. escape for table cells)
├── static/
│   └── person.html           # Person Lookup UI (embedded on Confluence)
├── docs/
│   ├── RUNNING_THE_REPORT.md                      # All ways to run report + Developer lookup
│   ├── GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md     # GitHub Actions + host lookup (Render/Railway)
│   └── RENDER_DEPLOY_CHECKLIST.md                 # Render deploy checklist (env vars, Docker, instance type)
├── requirements.txt
├── .env.example
├── deploy.sh                 # Deploy Cloud Function + Scheduler (sources .env)
├── main.tf                   # Optional: Terraform for function + scheduler
├── terraform.tfvars.example
├── Dockerfile                # Container image for Cloud Run / local
└── README.md
```

---

## Configuration

- **JQL filter:** `JIRA_CUSTOM_JQL_FILTER` in `team_config.py` is applied where relevant (e.g. report JQL, spillover fallback). Team report uses sprint + optional members; **Issues in Sprint** counts parent issues only (no subtasks).
- **Sprint metrics dashboard:** Set `VELOCITY_DASHBOARD_URL` in `.env` (or in GitHub Secrets when using Actions) to show a **Sprint metrics dashboard** link on the report.
- **Developer lookup / report links:** Set `APP_BASE_URL` (and optionally `VELOCITY_DASHBOARD_URL`) so the Confluence report shows the Developer lookup link and Sprint metrics dashboard link. When using GitHub Actions, add these as **repository secrets** so the scheduled report includes the links.
- **Sprint naming:** Report warns if the name doesn’t match `PI<X> S<X> Squad_Name`; it does not change behavior.
- **Rate limiting:** 15-second delay between teams when generating the report.
- **Confluence page:** Fixed title **"HSTG Digital Sprint status report"**. Each run updates that page if it exists; otherwise a new page is created.
- **Jira Verification Violations:** Per team: **doughnut chart** (counts by type) and **Per-issue details** table (Issue, Status, Assignee, Violations). Violation types: Missing +1 AC comment, Missing PR link, No time logged, No dev/local evidence, Branch Cleanup not mentioned, Concourse link not pasted, Rollback strategy not mentioned. PR link is detected in comments (including ADF), issuelinks, or Development/remotelinks.
- **Developer lookup:** Story point breakdowns (Sprint and This person's) are shown as **doughnut charts** alongside the cards.
- **AI summary (LLM):** The report’s **Blockers** section (Issues on Backlog, Issues on Hold) is generated by an LLM. The app supports **Groq** or **Gemini** (Google AI) via `LLM_PROVIDER`. For GCP/Vertex AI deployments, the recommended model is **Vertex AI Gemini 2.0 Flash** (best balance of quality and cost for summarization); **Gemini 2.0 Flash Lite** is a lower-cost option. See [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing).
- **Logging:** Most operational logs are at DEBUG. Use `logging.getLogger("jira_api.jira_sprint").setLevel(logging.DEBUG)` (or similar) to see spillover/board details.

---

## Troubleshooting

### "Failed to load developers" on Developer lookup page

The page shows the error in the Developer/Team area. Common causes:

- **Jira credentials:** Ensure `JIRA_USER_NAME` and `JIRA_TOKEN` in `.env` are correct and the user has access to the boards in `team_config.py`.
- **Network:** If the app runs in a restricted environment, Jira API calls may fail; check firewall/proxy.
- **Server crash:** If the server is crashing (see SIGSEGV below), the request to `/api/developers` may never complete. Use `--debug` when running locally.

### SIGSEGV when opening `/report` or when loading developers

The log shows `Worker (pid:...) was sent SIGSEGV!` and the browser may show "This page isn't working" / "ERR_EMPTY_RESPONSE". Try:

1. **Run with a single process (recommended for local):**
   ```bash
   functions_framework --target=generate_and_send_report --port=8080 --debug
   ```
   `--debug` uses the Flask dev server instead of multi-worker gunicorn, which often avoids this crash.

2. **Confirm the report runs outside the server:** Run `python main.py` in the same environment. If that completes without a crash, the issue is likely the HTTP worker process; `--debug` should help. If `python main.py` also crashes, the segfault is probably in a native dependency (e.g. Jira client, SSL, or LLM library). Try updating: `pip install --upgrade jira requests`.

3. **Check dependencies:** SIGSEGV can come from C extensions (e.g. in `cryptography`, `requests`). Ensure you're on a supported Python version and that packages are installed for your platform.

---

## Useful commands

| Task | Command |
|------|--------|
| Run report once | `python main.py` |
| Start web app (local) | `functions_framework --target=generate_and_send_report --port=8080 --debug` |
| Deploy to GCP | `./deploy.sh` |
| Trigger scheduler manually | `gcloud scheduler jobs run CLOUD_FUNCTION_NAME-scheduler --location=GCP_REGION` |
| View function logs | `gcloud functions logs read CLOUD_FUNCTION_NAME --region=GCP_REGION --limit=50` |
| Run report via GitHub Actions | Add secrets (see docs), then Actions → Jira Sprint Report → Run workflow |

---

## Documentation

| Doc | Description |
|-----|-------------|
| [RUNNING_THE_REPORT.md](docs/RUNNING_THE_REPORT.md) | All ways to run the report (local, web app, GCP, cron, GitHub Actions, HTTP) + how to use the Developer lookup page |
| [GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md](docs/GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md) | GitHub Actions for scheduled report (Mon & Thu 9 AM IST) + host Developer lookup on Render or Railway |
| [RENDER_DEPLOY_CHECKLIST.md](docs/RENDER_DEPLOY_CHECKLIST.md) | Step-by-step Render deploy (env vars, Docker, instance type, health check) |

---

## License

Internal use. Adjust `team_config.py` and `.env` for your org.
