# Step-by-step: GitHub Actions (report) + Developer lookup hosted and working

This guide gets you to:

1. **Run the sprint report on a schedule** (Monday & Thursday 9 AM IST) using **GitHub Actions**.
2. **Host the Developer lookup page** so anyone can open a URL, enter developer name / team / sprint, and fetch details from Jira. The app is deployed from your GitHub repo to **Render** (free tier), so the lookup is “hosted” and always available at a public URL.

**Why two parts?** GitHub Actions runs **jobs** (start → run → exit). It does not run a long-lived web server. So:

- **Report:** A scheduled workflow runs `python main.py` (or calls your deployed app’s `/report`).  
- **Developer lookup:** The same app is **deployed to Render** so the HTML page and APIs (`/api/developers`, `/api/sprints`, `/api/person`) are served at a URL.

After setup you will have:

- **Report:** Runs automatically Mon & Thu 9 AM IST (and manually from the Actions tab).
- **Developer lookup:** Live at `https://your-app-name.onrender.com/` (or the URL Render gives you).
- **Confluence:** Can show the “Developer lookup” link pointing to that URL (via `APP_BASE_URL`).

---

## Why not GitHub Pages?

**GitHub Pages only serves static files** (HTML, CSS, JavaScript). It does not run any server or backend. There is no Python, no environment variables at runtime, and no API routes.

The **Developer lookup** page needs a **backend** that:

- Calls **Jira** with credentials (`JIRA_USER_NAME`, `JIRA_TOKEN`) to:
  - List developers (assignees from each team’s sprint) → `/api/developers`
  - List sprints for a board → `/api/sprints`
  - Fetch a person’s story points and issues → `/api/person`
- Uses **team config** (e.g. `team_config.py` / board IDs) and keeps secrets out of the browser.

That logic runs in your **Python app**. GitHub Pages cannot run it. So we need a host that runs the app (Render, Railway, Cloud Run, etc.).

**Could we use GitHub Pages for the UI and something else for the API?**  
Yes: you could put the **static** HTML/JS on Pages and deploy **only the API** (e.g. as a serverless function or a small service) elsewhere. The page would then call that API URL from JavaScript. You’d still need a backend somewhere (Render, a Cloud Function, etc.); Pages would only serve the frontend. That’s more setup (CORS, two URLs, wiring the frontend to the API URL), so this guide uses a single deployment (Render) that serves both the page and the APIs.

---

## Faster, always-on alternative to Render (recommended)

Render’s **free tier** spins down the app after ~15 minutes of no traffic. The first request after that has a **cold start** (30–60 seconds). To get a **faster** site that **does not go down** when idle, you need a host that keeps the app running.

**Best option: Railway (Hobby plan, ~$5/month)**

- **Always on:** On the Hobby plan you can **disable “Serverless”** (sleep) per service, so the app stays running 24/7. No cold starts.
- **Fast:** Good response times; deploy is similar to Render (connect repo, set env vars, deploy).
- **Cost:** About $5/month (Hobby); you pay for usage within the plan.

| Option | Always on? | Cold start on free? | Cost (always-on) |
|--------|------------|----------------------|-------------------|
| **Railway (Hobby)** | Yes (disable Serverless) | N/A | ~$5/mo |
| **Render (free)** | No (spins down ~15 min) | Yes, 30–60 s | Free |
| **Render (paid)** | Yes | No | ~$7+/mo |
| **Fly.io** | Yes (if you run a machine 24/7) | No once running | Pay per use, ~$3–5/mo for a tiny VM |
| **Google Cloud Run (min instances = 1)** | Yes | No | Pay for 1 idle instance (~$10–20/mo) |

**Recommendation:** Use **Railway** with the Hobby plan and **Serverless (sleep) turned off** for the Developer lookup service. Steps are in **Part 3b** below. If you prefer to stay on Render, upgrade to a **paid** web service so the instance stays up and does not spin down.

---

## Part 1: GitHub repository and secrets

### Step 1.1: Push your code to GitHub

If the Jira report repo is not on GitHub yet:

1. Create a new repository on GitHub (e.g. `jira-sprint-report`).
2. In your local project folder:
   ```bash
   git remote add origin https://github.com/YOUR_ORG/jira-sprint-report.git
   git push -u origin main
   ```
   (Use your actual org/repo and branch name.)

### Step 1.2: Add repository secrets (for the scheduled report)

These secrets are used by the GitHub Actions workflow when it runs the report.

1. In GitHub: open your repo → **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** and add each of these (names must match exactly):

| Secret name | Required | Description |
|-------------|----------|-------------|
| `JIRA_USER_NAME` | Yes | Jira user email |
| `JIRA_TOKEN` | Yes | Jira API token |
| `TEAMS_WEBHOOK` | Yes | Teams incoming webhook URL |
| `CONFLUENCE_BASE_URL` | Yes | e.g. `https://your-site.atlassian.net/wiki` |
| `CONFLUENCE_USER` | Yes | Confluence user email |
| `CONFLUENCE_TOKEN` | Yes | Confluence API token |
| `CONFLUENCE_SPACE_KEY` | Yes | Confluence space key (e.g. `HGD`) |
| `LLM_PROVIDER` | No | `groq` or `gemini` (default: groq) |
| `GROQ_API_KEY` | If groq | Groq API key |
| `GEMINI_API_KEY` | If gemini | Gemini/Google AI key |
| `JIRA_SERVER` | No | Jira base URL (default: `https://hy-vee.atlassian.net`) |
| **`APP_BASE_URL`** | For report links | URL of your Developer lookup app (Render/Railway) **with no trailing slash**. Needed so the Confluence report shows the “Developer lookup” link. |
| **`VELOCITY_DASHBOARD_URL`** | Optional | URL for the “Sprint metrics dashboard” link on the report. |

3. Save each secret. Do **not** put these in the repo code or in a committed file.

---

## Part 2: GitHub Actions workflow (scheduled report)

The workflow file is already in the repo at `.github/workflows/jira-report.yml`. It:

- Runs on a **schedule**: 03:30 UTC on Monday and Thursday (= 9:00 AM IST).
- Can be **run manually**: Actions → Jira Sprint Report → Run workflow.

### Step 2.1: Confirm the workflow file

Open `.github/workflows/jira-report.yml`. It should look like this (you can keep the comment block as-is):

```yaml
name: Jira Sprint Report

on:
  schedule:
    - cron: '30 3 * * 1,4'   # 9 AM IST Mon & Thu
  workflow_dispatch:

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run report
        env:
          JIRA_SERVER: ${{ secrets.JIRA_SERVER || 'https://hy-vee.atlassian.net' }}
          JIRA_USER_NAME: ${{ secrets.JIRA_USER_NAME }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
          TEAMS_WEBHOOK: ${{ secrets.TEAMS_WEBHOOK }}
          CONFLUENCE_BASE_URL: ${{ secrets.CONFLUENCE_BASE_URL }}
          CONFLUENCE_USER: ${{ secrets.CONFLUENCE_USER }}
          CONFLUENCE_TOKEN: ${{ secrets.CONFLUENCE_TOKEN }}
          CONFLUENCE_SPACE_KEY: ${{ secrets.CONFLUENCE_SPACE_KEY }}
          LLM_PROVIDER: ${{ secrets.LLM_PROVIDER || 'groq' }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python main.py
```

### Step 2.2: Run the report once manually

1. In GitHub: **Actions** → **Jira Sprint Report** → **Run workflow** → **Run workflow**.
2. When the job finishes, open the run and check the “Run report” step for errors.
3. Confirm in Confluence and Teams that the report and link were posted.

After this, the report will also run automatically every Mon & Thu at 9 AM IST.

---

## Part 3: Host the Developer lookup (deploy app to Render)

Render gives you a public URL where your app runs. That URL serves the Developer lookup page and APIs. You will add the same environment variables (as in GitHub Secrets) to Render so the app can call Jira, Confluence, and Teams.

### Step 3.1: Create a Render account and connect GitHub

1. Go to [render.com](https://render.com) and sign up (or log in).
2. Connect your GitHub account: **Account Settings** → **Integrations** → connect GitHub and authorize the org/repo that contains this project.

### Step 3.2: Create a new Web Service

1. In the Render dashboard click **New +** → **Web Service**.
2. Connect the repository that contains this Jira app (e.g. `jira-sprint-report`).
3. Use the same branch you use for the Actions workflow (e.g. `main`).

### Step 3.3: Configure the Web Service

Set these in the Render form (or in the service’s **Environment** tab after creation):

| Field | Value |
|-------|--------|
| **Name** | e.g. `jira-developer-lookup` (any name you like) |
| **Region** | Choose one close to your users |
| **Branch** | `main` (or your default branch) |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `functions_framework --target=generate_and_send_report --port=$PORT` |

Important: **Start Command** must use `$PORT` so Render can route traffic to your app.

### Step 3.4: Add environment variables on Render

In the same Web Service, go to **Environment** and add the same variables you use in the report (same as GitHub Secrets). Add them as **Environment Variables** (or **Secret Files** only if you need a file). Suggested list:

| Key | Value | Notes |
|-----|--------|--------|
| `JIRA_USER_NAME` | (your Jira email) | Required |
| `JIRA_TOKEN` | (your Jira API token) | Required, mark **Secret** |
| `JIRA_SERVER` | `https://hy-vee.atlassian.net` | Or your Jira base URL |
| `CONFLUENCE_BASE_URL` | (e.g. https://your-site.atlassian.net/wiki) | Required |
| `CONFLUENCE_USER` | (Confluence email) | Required |
| `CONFLUENCE_TOKEN` | (Confluence API token) | Required, Secret |
| `CONFLUENCE_SPACE_KEY` | (e.g. HGD) | Required |
| `TEAMS_WEBHOOK` | (Teams webhook URL) | Required, Secret |
| `LLM_PROVIDER` | `groq` or `gemini` | Optional |
| `GROQ_API_KEY` | (key) | If using groq, Secret |
| `GEMINI_API_KEY` | (key) | If using gemini, Secret |
| `APP_BASE_URL` | **Leave empty for now** | You’ll set it after first deploy |

You can copy these from your local `.env` (never commit `.env`). Mark sensitive values as **Secret** in Render.

### Step 3.5: Deploy

1. Click **Create Web Service**. Render will build and deploy.
2. Wait until the deploy finishes and the service shows **Live** (green).
3. Open the service URL (e.g. `https://jira-developer-lookup.onrender.com`). You should see the **Developer lookup** page (title “Developer lookup”, with Developer / Team / Sprint fields).

### Step 3.6: Set APP_BASE_URL (for Confluence link)

1. In Render, open your Web Service → **Environment**.
2. Add or edit:
   - **Key:** `APP_BASE_URL`
   - **Value:** Your Render service URL **with no trailing slash**, e.g. `https://jira-developer-lookup.onrender.com`
3. Save. Render will redeploy. After that, when the **report** runs (from GitHub Actions), the Confluence page will show the “Developer lookup” link pointing to this URL.

### Step 3.7: (Optional) Allow report to run from the same URL

The same deployed app can also run the report if someone (or a scheduler) opens:

`https://your-app-name.onrender.com/report`

You do not have to use this for the schedule (GitHub Actions already runs the report). If you want to trigger the report from the browser or another scheduler, just open that URL. No extra config is required.

---

## Part 3b: Host on Railway (always-on, no spin-down) — recommended for speed

Use this instead of (or after) Part 3 if you want the Developer lookup to stay up 24/7 with no cold starts. You need a **Railway account** and the **Hobby plan** (~$5/month) so you can turn off sleep.

### Step 3b.1: Create a Railway project from GitHub

1. Go to [railway.app](https://railway.app) and sign in (GitHub).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select your Jira report repo and the branch (e.g. `main`). Railway will create a new service.

### Step 3b.2: Configure the service

1. Open the new service → **Settings** (or **Variables**).
2. **Build:**
   - **Builder:** Nixpacks (default) or set **Build Command** to: `pip install -r requirements.txt`
3. **Start:**
   - **Start Command:** `functions_framework --target=generate_and_send_report --port=$PORT`
   - Railway sets `PORT`; the app must listen on it.

### Step 3b.3: Add environment variables

In the same service, go to **Variables** and add the same variables as in the Render section (and in GitHub Secrets):

- `JIRA_USER_NAME`, `JIRA_TOKEN`, `JIRA_SERVER`
- `CONFLUENCE_BASE_URL`, `CONFLUENCE_USER`, `CONFLUENCE_TOKEN`, `CONFLUENCE_SPACE_KEY`
- `TEAMS_WEBHOOK`
- `LLM_PROVIDER`, `GROQ_API_KEY` or `GEMINI_API_KEY`

Mark secrets as **sensitive** where possible. Do **not** set `APP_BASE_URL` until after the first deploy (then set it to your Railway URL with no trailing slash).

### Step 3b.4: Turn off sleep (always-on)

1. In the service, go to **Settings** → **Deploy** (or **Serverless** / **Sleep**).
2. Find **Serverless** or **Sleep** or **Scale to zero**.
3. **Disable** it so the service stays running 24/7. (This option is available on the Hobby plan; free tier keeps sleep on.)

### Step 3b.5: Deploy and get the URL

1. Trigger a deploy (push to the repo or **Deploy** in Railway). Wait until it’s **Active**.
2. Open **Settings** → **Networking** (or **Generate domain**) and add a **public domain**. Railway will give you a URL like `https://your-service.up.railway.app`.
3. Open that URL in a browser — you should see the Developer lookup page.
4. Set **`APP_BASE_URL`** in Variables to that URL (no trailing slash), then redeploy so the Confluence report’s “Developer lookup” link uses it.

GitHub Actions is unchanged: the **report** still runs on schedule from the workflow. Only the **Developer lookup** host is Railway instead of Render, and it stays on so the site is faster and doesn’t go down when idle.

---

## Part 4: Use the Developer lookup

1. Open your app URL (Render, e.g. `https://jira-developer-lookup.onrender.com/`, or Railway, e.g. `https://your-service.up.railway.app/`, or `.../person`).
2. In **Developer**, type a name and choose from the list (loaded from Jira).
3. **Team** will fill automatically; choose a **Sprint** from the dropdown.
4. Click **Fetch from Jira**.
5. You should see story points (Total, Not started, In progress, Completed, Blocked/On hold) and the issue list.

If “Failed to load developers” appears, check the host’s **Logs** (Render or Railway) and that all Jira/Confluence/Teams env vars are set correctly.

---

## Part 5: Quick checklist

- [ ] Repo is on GitHub and code is pushed.
- [ ] All required **repository secrets** are set (Jira, Confluence, Teams, LLM).
- [ ] **Actions** → **Jira Sprint Report** → **Run workflow** runs successfully and the report appears in Confluence/Teams.
- [ ] **Render** or **Railway** service is created with correct **Build** and **Start** commands and uses `$PORT`.
- [ ] Same env vars (and secrets) are set on the host. On Railway (Hobby), Serverless/sleep is **disabled** so the app stays on.
- [ ] Developer lookup page loads at the app URL and “Fetch from Jira” works.
- [ ] `APP_BASE_URL` is set to that URL (no trailing slash) so the Confluence report shows the correct Developer lookup link.

---

## Notes

- **Render free tier:** The service may spin down after ~15 minutes of no traffic. The first request after that can take 30–60 seconds (“cold start”). For a **faster, always-on** site, use **Railway (Hobby)** and disable Serverless (Part 3b), or use Render/Cloud Run paid with the app always running.
- **Secrets in two places:** GitHub Secrets are used only by the Actions workflow when it runs the report. The host’s env vars (Render or Railway) are used by the live app for Developer lookup (and for `/report` if you call it from the browser). You can use the same values in both; keep them secret and never commit them.
- **Alternative hosts:** **Railway (Hobby)** is recommended for always-on and speed. You can also use **Fly.io** (pay per use, small always-on VM) or **Google Cloud Run** with min instances = 1. Deploy this repo as a web service, set the same env vars, and use `--port=$PORT` (or the platform’s equivalent).

For more ways to run the report and use Developer lookup, see [RUNNING_THE_REPORT.md](RUNNING_THE_REPORT.md).
