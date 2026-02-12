# Step-by-step: All ways to run the Jira report

This guide has two parts:

1. **Ways to run the sprint report** — Generate the report → post to Confluence → notify Teams (including scheduled options: Mon & Thu 9 AM IST).
2. **Using the Developer lookup page** — The HTML page where a user enters developer name, team, and sprint, then fetches that person’s Jira details (story points and issues).

---

## Shared setup (do once)

All methods need the same configuration.

### 1. Clone and install

```bash
cd /path/to/Jira
pip install -r requirements.txt
```

### 2. Environment variables

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
| `TEAMS_WEBHOOK` | For report | Teams incoming webhook URL |
| `LLM_PROVIDER` | For report | `groq` or `gemini` (required for Blockers comment summaries) |
| `GROQ_API_KEY` | If groq | Groq API key (required when `LLM_PROVIDER=groq`) |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | If gemini | Gemini/Google AI key (required when `LLM_PROVIDER=gemini`) |

Optional: `JIRA_SERVER`, `VELOCITY_DASHBOARD_URL`, `APP_BASE_URL` (for Developer lookup link on Confluence).

### 3. Team configuration

Edit `team_config.py`: set `board_id` for each team. Use `members: []` to auto-detect assignees from the sprint.

---

## Method 1: Run once locally (no server)

**Use when:** You want to generate the report once from your machine and exit.

### Steps

1. Complete **Shared setup** above.
2. In the project directory, run:

   ```bash
   python main.py
   ```

3. The script will:
   - Fetch data from Jira for each team
   - Build the report and post it to Confluence
   - Send the Confluence link to Teams
   - Then exit

**Note:** When you run `python main.py`, the code uses an internal “mock” request with path `/report`, so it always runs the full report (not the Person Lookup UI).

---

## Method 2: Web app locally — run report from the browser

**Use when:** You want a local server with Developer lookup and a “Run report” button in the browser.

### Steps

1. Complete **Shared setup** above.
2. Start the web app:

   ```bash
   functions_framework --target=generate_and_send_report --port=8080 --debug
   ```

   Keep the terminal open. Use `--debug` to avoid multi-worker issues (e.g. SIGSEGV) when hitting `/report`.

3. In your browser:
   - **Developer lookup:** http://localhost:8080/ or http://localhost:8080/person  
   - **Run the report:** http://localhost:8080/report  

4. When you open `/report`, the app runs the full report (same as Method 1) and returns a short summary in the browser.

**Optional:** Set `APP_BASE_URL=http://localhost:8080` in `.env` if you want the Confluence report to show the Developer lookup link pointing to your local app.

---

## Method 3: Google Cloud — Cloud Function + Cloud Scheduler

**Use when:** You want the report to run automatically on a schedule (e.g. Mon & Thu 9 AM IST) without keeping your machine on.

### Steps

1. Complete **Shared setup** above.
2. In `.env`, add GCP variables:
   - `GCP_PROJECT_ID` — your GCP project ID  
   - `GCP_REGION` — e.g. `us-central1`  
   - `CLOUD_FUNCTION_NAME` — e.g. `jira-daily-report`  
   Keep all Jira, Confluence, Teams, and LLM vars set.

3. (Optional) Set schedule before first deploy. Default is **Mon & Thu 9 AM IST**:
   ```bash
   SCHEDULE_CRON="0 9 * * 1,4"    # 1=Mon, 4=Thu
   SCHEDULE_TIMEZONE=Asia/Kolkata
   ```

4. Deploy:
   ```bash
   ./deploy.sh
   ```

5. Copy the **Function URL** from the script output. To show the Developer lookup link on the Confluence report:
   - Set in `.env`: `APP_BASE_URL=<that URL>` (no trailing slash)
   - Run `./deploy.sh` again so the function gets `APP_BASE_URL`

6. **Trigger report manually (optional):**
   ```bash
   gcloud scheduler jobs run CLOUD_FUNCTION_NAME-scheduler --location=GCP_REGION
   ```
   Or open in browser: `https://YOUR_FUNCTION_URL/report`

**What you get:** A Cloud Function that serves the app and a Cloud Scheduler job that calls `/report` at the configured times.

---

## Method 4: Google Cloud — Terraform (function + scheduler)

**Use when:** You prefer Terraform over `deploy.sh` for GCP.

### Steps

1. Complete **Shared setup** above (Terraform will not read `.env` for the function; you pass values via `terraform.tfvars`).
2. Copy the Terraform example and edit:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```
   Fill in `project_id`, `region`, `jira_user_name`, `jira_token`, `teams_webhook`, Confluence vars, and LLM keys. Optionally set `schedule` and `time_zone` (e.g. `"0 9 * * 1,4"` and `"Asia/Kolkata"`).
3. From the project root:
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```
4. Note the function URL from Terraform outputs. To add Developer lookup link on Confluence you’d need to pass `APP_BASE_URL` into the function (e.g. via a Terraform variable and env in the function config) and re-apply.

**What you get:** Same as Method 3 (Cloud Function + Cloud Scheduler), managed by Terraform.

---

## Method 5: Cron on a server or VM

**Use when:** You have a Linux/macOS server or VM that is always on (or a machine you leave on). No GCP/AWS needed.

### Steps

1. On the server, complete **Shared setup**: clone repo, `pip install -r requirements.txt`, create `.env`, edit `team_config.py`.
2. Confirm Python path, e.g.:
   ```bash
   which python3
   ```
3. Open crontab:
   ```bash
   crontab -e
   ```
4. Add one line (choose the one that matches your server’s timezone).

   **If the server uses IST (Asia/Kolkata):**  
   Run at 9:00 AM on Monday and Thursday:
   ```bash
   0 9 * * 1,4 cd /path/to/Jira && /usr/bin/python3 main.py
   ```
   Replace `/path/to/Jira` with the real path and `/usr/bin/python3` with `which python3` if different.

   **If the server uses UTC:**  
   9 AM IST = 03:30 UTC. Run at 03:30 UTC on Monday and Thursday:
   ```bash
   30 3 * * 1,4 cd /path/to/Jira && /usr/bin/python3 main.py
   ```

5. Save and exit. The report will run at the given times.

**Cron format reminder:** `minute hour day-of-month month day-of-week`. Day of week: 0=Sun, 1=Mon, …, 4=Thu, 6=Sat.

---

## Method 6: GitHub Actions (scheduled or manual)

**Use when:** The repo is on GitHub and you’re okay running the report in CI (no GCP required for scheduling).

### Steps

1. The workflow file is already in the repo: `.github/workflows/jira-report.yml`. It runs at **03:30 UTC on Mon & Thu** (= 9 AM IST).
2. In your GitHub repo: **Settings → Secrets and variables → Actions**. Add these secrets (same as `.env`). For the **Developer lookup** and **Sprint metrics dashboard** links to appear in the Confluence report, also add **`APP_BASE_URL`** (your hosted app URL, no trailing slash) and optionally **`VELOCITY_DASHBOARD_URL`**. For a full walkthrough that also hosts the Developer lookup at a public URL, see [GitHub Actions + Developer lookup (Render/Railway)](GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md).
   - `JIRA_USER_NAME`
   - `JIRA_TOKEN`
   - `TEAMS_WEBHOOK`
   - `CONFLUENCE_BASE_URL`
   - `CONFLUENCE_USER`
   - `CONFLUENCE_TOKEN`
   - `CONFLUENCE_SPACE_KEY`
   - `LLM_PROVIDER` (required; e.g. groq or gemini)
   - `GROQ_API_KEY` and/or `GEMINI_API_KEY`
   - Optional: `JIRA_SERVER` (defaults to `https://hy-vee.atlassian.net` in the workflow)
3. Push to the default branch (or the branch the workflow is on). The scheduled runs will happen automatically.
4. **Manual run:** In the repo go to **Actions → Jira Sprint Report → Run workflow**.

**What you get:** Report runs in GitHub’s runner; no server or GCP scheduler to maintain.

---

## Method 7: Manual trigger via HTTP (when the app is already deployed)

**Use when:** The app is already running somewhere (e.g. Cloud Function, Cloud Run, or your own server) and you only want to trigger the report on demand.

### Steps

1. You need the **base URL** of the deployed app (e.g. `https://REGION-PROJECT.cloudfunctions.net/jira-daily-report`).
2. Trigger the report with a GET request:
   - **Browser:** Open `https://YOUR_APP_BASE_URL/report`
   - **curl:**
     ```bash
     curl "https://YOUR_APP_BASE_URL/report"
     ```

The app will run the full report (Jira → Confluence → Teams) and return a short summary. No extra setup if the deployment already has the right env vars.

---

## Method 8: Cloud Run Job + Cloud Scheduler (GCP, no Cloud Function)

**Use when:** You want to stay on GCP but run the report as a “job” (run to completion) instead of an HTTP Cloud Function.

### Steps (outline)

1. Build and push the app image (e.g. using the repo’s `Dockerfile`) to Artifact Registry or Container Registry.
2. Create a **Cloud Run Job** that runs the same container with the same env vars, and set the command to run the report (e.g. `python main.py` or a small script that does the equivalent of calling the report logic).
3. Create a **Cloud Scheduler** HTTP job that targets the Cloud Run Jobs API to start the job (or use a pub/sub → Cloud Run Job trigger if you prefer).
4. Set the schedule to your desired cron (e.g. `0 9 * * 1,4` in `Asia/Kolkata`).

Detailed commands depend on your image name and project; the idea is: **scheduler triggers → Cloud Run Job runs once → job runs `main.py` (or report entrypoint) with env vars**.

---

## Quick reference

| Method | Schedule? | Needs GCP? | Best for |
|--------|-----------|------------|----------|
| 1. Run once locally | No | No | One-off run from your machine |
| 2. Web app locally | No (you hit /report) | No | Local dev + manual report run |
| 3. Cloud Function + Scheduler | Yes (Mon & Thu 9 AM IST) | Yes | Fully managed schedule on GCP |
| 4. Terraform (GCP) | Yes (configurable) | Yes | IaC for function + scheduler |
| 5. Cron on server | Yes (you set cron) | No | Your own VM or server |
| 6. GitHub Actions | Yes (Mon & Thu 9 AM IST) | No | Repo on GitHub, no server |
| 7. HTTP trigger | No (on demand) | Depends where app runs | Ad-hoc run when app is deployed |
| 8. Cloud Run Job + Scheduler | Yes | Yes | GCP with job instead of function |

---

## Using the Developer lookup page (HTML)

The app includes an **HTML page** where someone can look up a developer, pick their team and sprint, and **fetch their Jira details** (story points by status and issue list). This is separate from “running the report”; the report is a batch job, while Developer lookup is interactive.

### What the user gets

- **Story points breakdown:** Total, Not started, In progress, Completed, Blocked/On hold (for the selected developer in the selected sprint).
- **Doughnut charts** for that breakdown and for the sprint overall.
- **Issue list:** Each issue with key, summary, status, story points, and link to Jira.

### Where to open the Developer lookup page

The same app serves both the report and the Developer lookup page. So you open the page at the **base URL** of wherever the app is running:

| Where the app runs | Developer lookup URL |
|--------------------|----------------------|
| **Local web app** (Method 2) | http://localhost:8080/ or http://localhost:8080/person |
| **GCP Cloud Function** (Method 3 or 4) | `https://YOUR_FUNCTION_URL/` or `https://YOUR_FUNCTION_URL/person` |
| **Your own server** (e.g. after Method 5 cron) | `https://your-server/` or `https://your-server/person` |

If you only run **Method 1** (`python main.py` once), there is **no server** running, so the Developer lookup page is not available until you start the web app (Method 2) or use a deployed URL.

### Step-by-step: how a user uses the page

1. **Open the page**  
   Go to the Developer lookup URL (see table above). The page title is **“Developer lookup”** and the subtitle says to search by developer name and pick team/sprint.

2. **Enter developer name**  
   In the **Developer** field, start typing a name. The list of developers is loaded from Jira (assignees from each team’s sprint). Pick the developer from the dropdown that appears.

3. **Team**  
   After you select a developer, the **Team** field is filled automatically (read-only) based on the teams that developer belongs to. If a developer appears in more than one team, you may need to pick the right one from the dropdown if the UI supports it; otherwise the first match is used.

4. **Select sprint**  
   In the **Sprint** dropdown, choose the sprint you want. The list is loaded from Jira for that board (and optionally filtered by the selected developer). Options usually include active, future, and recent closed sprints.

5. **Fetch from Jira**  
   Click **“Fetch from Jira”**. The page calls the app’s `/api/person` endpoint with the selected developer, board (team), and sprint. The app fetches data from Jira and returns story points breakdown and issues.

6. **View results**  
   Below the form you’ll see:
   - **Sprint** and **developer** summary (e.g. sprint name, total story points).
   - **Story points:** Total, Not started, In progress, Completed, Blocked/On hold (with doughnut charts).
   - **Issue table:** Key (link to Jira), summary, status, story points.

### Prerequisites for Developer lookup

- The **app must be running** (web app locally or deployed) so the HTML and APIs are available.
- **Jira credentials** must be set in the app’s environment (e.g. `JIRA_USER_NAME`, `JIRA_TOKEN` in `.env` or in the deployment). The app uses these to call Jira on behalf of the user.
- **Team configuration** (`team_config.py`) must have the correct `board_id` for each team; the developer list and sprints come from those boards.

### Link on the Confluence report

When the **sprint report** is posted to Confluence, it can include a **“Developer lookup”** link that points to this same HTML page. For that link to work:

- Set **`APP_BASE_URL`** in the app’s environment to the public URL of the app (e.g. `https://YOUR_FUNCTION_URL` with no trailing slash).
- After deployment, the Confluence report will show a link like “Open Developer lookup in a new tab” and optionally an iframe. Users can use that link instead of memorizing the app URL.

---

## Troubleshooting

- **Report not posting to Confluence:** Check `CONFLUENCE_*` in `.env` (or in GitHub Secrets / Terraform vars). Ensure the Confluence user has permission to create/update pages in the space.
- **No Teams message:** Check `TEAMS_WEBHOOK`. If unset, the app still runs but skips posting to Teams.
- **“Missing JIRA_USER_NAME or JIRA_TOKEN”:** Ensure `.env` is in the project root and loaded (e.g. `python-dotenv` loads it when you run `main.py` or the app).
- **Scheduler not firing (GCP):** In Cloud Console, check Cloud Scheduler and the function’s logs. Ensure the job’s URL is `.../report` and the timezone is correct (e.g. `Asia/Kolkata`).
- **GitHub Actions failing:** Check Actions run logs. Ensure all required secrets are set and that the workflow file is on the branch that runs the schedule.
- **Developer lookup: “Failed to load developers” or empty list:** The app must be running and have valid Jira credentials (`JIRA_USER_NAME`, `JIRA_TOKEN`) with access to the boards in `team_config.py`. Check the browser Network tab for `/api/developers` and the app logs for errors.

For more, see the main [README](../README.md) and the **Troubleshooting** section there.
