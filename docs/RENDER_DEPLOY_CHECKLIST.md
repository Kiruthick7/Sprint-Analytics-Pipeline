# Render deploy checklist – Jira Developer Lookup

Use this while you’re on the **New Web Service** page (or the service’s **Settings** after creation). For other ways to run the report and host the app, see the main [README](../README.md) and [GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md](GITHUB_ACTIONS_AND_DEVELOPER_LOOKUP.md).

---

## 1. Source and name

- **Source:** Your GitHub repo (e.g. `your-org/Jira` or `Jira_Status_Report_Tool`)
- **Name:** e.g. `Jira-Developer-Lookup`
- **Branch:** `main`
- **Region:** Virginia (or your choice)

---

## 2. Language / runtime

Render detected **Docker** because you have a `Dockerfile`. That’s correct — leave **Language** as **Docker**.

- **Dockerfile Path:** `./Dockerfile` (default)
- **Docker Command:** leave **empty** (the Dockerfile already runs `functions_framework --target=generate_and_send_report --port=$PORT`)

---

## 3. Instance type

- **Free:** App will **spin down** after ~15 min idle → first load can take 30–60 s.
- **Starter ($7/mo):** App stays **on** → no spin-down, faster and more reliable.

Choose **Starter** if you want the site to stay up and fast.

---

## 4. Environment variables (required)

Click **“Add Environment Variable”** (or “Add from .env”) and add these. Mark secrets as **Secret** where the UI allows.

| Key | Value | Secret? |
|-----|--------|--------|
| `JIRA_USER_NAME` | Your Jira user email | No |
| `JIRA_TOKEN` | Your Jira API token | **Yes** |
| `JIRA_SERVER` | `https://hy-vee.atlassian.net` (or your Jira URL) | No |
| `CONFLUENCE_BASE_URL` | e.g. `https://your-site.atlassian.net/wiki` | No |
| `CONFLUENCE_USER` | Confluence user email | No |
| `CONFLUENCE_TOKEN` | Confluence API token | **Yes** |
| `CONFLUENCE_SPACE_KEY` | e.g. `HGD` | No |
| `TEAMS_WEBHOOK` | Teams incoming webhook URL | **Yes** |
| `LLM_PROVIDER` | `groq` or `gemini` | No |
| `GROQ_API_KEY` | Groq API key (if using groq) | **Yes** |
| `GEMINI_API_KEY` | Gemini API key (if using gemini) | **Yes** |

**Do not set `APP_BASE_URL` yet.** Set it after the first deploy to your service URL (no trailing slash).

---

## 5. Advanced (optional)

- **Health Check Path:** Change from `/healthz` to **`/`** (your app has no `/healthz`; `/` serves the Developer lookup page). Or leave blank if Render allows.
- **Docker Build Context Directory:** leave default (`.`)
- **Dockerfile Path:** `./Dockerfile`
- **Docker Command:** leave empty
- **Pre-Deploy Command:** leave empty (only for paid; you don’t need it)
- **Auto-Deploy:** keep **On Commit** if you want deploys on every push

---

## 6. Deploy

1. Commit and push the **Dockerfile** change (we added `COPY utils/ utils/`) so the build doesn’t fail.
2. Click **“Deploy Web Service”**.
3. Wait for the build and deploy to finish (Logs will show progress).
4. Open the service URL Render gives you (e.g. `https://jira-developer-lookup.onrender.com`). You should see the **Developer lookup** page.
5. In the service **Environment** tab, add:
   - **Key:** `APP_BASE_URL`
   - **Value:** Your service URL with **no** trailing slash, e.g. `https://jira-developer-lookup.onrender.com`
6. Save so the app redeploys. The Confluence report will then show the correct “Developer lookup” link.

---

## 7. Quick test

- Open the service URL → Developer lookup page.
- Type a developer name → select team and sprint → **Fetch from Jira**.
- If “Failed to load developers” appears, check **Logs** and that all env vars (especially Jira) are set correctly.

---

## Summary

| Step | Action |
|------|--------|
| 1 | Keep Docker; Dockerfile path `./Dockerfile`; Docker Command empty |
| 2 | Pick **Starter** ($7/mo) if you want always-on and no spin-down |
| 3 | Add all **environment variables** (Jira, Confluence, Teams, LLM) |
| 4 | Set Health Check Path to **`/`** (or leave default and ignore health) |
| 5 | Push the repo (with Dockerfile fix), then click **Deploy Web Service** |
| 6 | After deploy, add **`APP_BASE_URL`** = your service URL (no trailing slash) |
