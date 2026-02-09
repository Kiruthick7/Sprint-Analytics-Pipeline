#!/bin/bash

# Deploy Jira Daily Report as Cloud Function + Cloud Scheduler (Monday 9 AM IST).
# See README "How to run" → Step 5 for full steps. Requires .env with GCP_*, Jira, Confluence, Teams, LLM.

set -e  # Exit on error

# Load environment variables
source .env

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Deploying Jira Daily Report to GCP${NC}"
echo -e "${BLUE}========================================${NC}"

# Validate required environment variables
if [ -z "$GCP_PROJECT_ID" ] || [ -z "$GCP_REGION" ]; then
    echo -e "${RED}Error: GCP_PROJECT_ID and GCP_REGION must be set${NC}"
    exit 1
fi

# Set GCP project
echo -e "\n${GREEN}Setting GCP project...${NC}"
gcloud config set project $GCP_PROJECT_ID

# Enable required APIs
echo -e "\n${GREEN}Enabling required GCP APIs...${NC}"
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable cloudbuilder.googleapis.com
gcloud services enable aiplatform.googleapis.com

# Build env vars for the function (required: Jira, Teams; optional: Confluence, LLM, APP_BASE_URL)
# Set these in .env before deploying. Optional vars can be left empty and configured later in GCP Console.
ENV_VARS="JIRA_USER_NAME=${JIRA_USER_NAME},JIRA_TOKEN=${JIRA_TOKEN},JIRA_SERVER=${JIRA_SERVER:-https://hy-vee.atlassian.net},TEAMS_WEBHOOK=${TEAMS_WEBHOOK}"
ENV_VARS="${ENV_VARS},CONFLUENCE_BASE_URL=${CONFLUENCE_BASE_URL:-},CONFLUENCE_USER=${CONFLUENCE_USER:-},CONFLUENCE_TOKEN=${CONFLUENCE_TOKEN:-},CONFLUENCE_SPACE_KEY=${CONFLUENCE_SPACE_KEY:-}"
ENV_VARS="${ENV_VARS},LLM_PROVIDER=${LLM_PROVIDER:-groq},GROQ_API_KEY=${GROQ_API_KEY:-},GEMINI_API_KEY=${GEMINI_API_KEY:-},APP_BASE_URL=${APP_BASE_URL:-}"

# Deploy Cloud Function
echo -e "\n${GREEN}Deploying Cloud Function...${NC}"
gcloud functions deploy ${CLOUD_FUNCTION_NAME} \
    --gen2 \
    --runtime=python311 \
    --region=${GCP_REGION} \
    --source=. \
    --entry-point=generate_and_send_report \
    --trigger-http \
    --allow-unauthenticated \
    --timeout=540s \
    --memory=512MB \
    --set-env-vars="${ENV_VARS}"

# Get the Cloud Function URL (no trailing slash)
FUNCTION_URL=$(gcloud functions describe ${CLOUD_FUNCTION_NAME} \
    --region=${GCP_REGION} \
    --format='value(serviceConfig.uri)')
FUNCTION_URL=${FUNCTION_URL%/}

echo -e "\n${GREEN}Cloud Function deployed successfully!${NC}"
echo -e "Function URL: ${FUNCTION_URL}"

# Cloud Scheduler: run the REPORT (not the person lookup). Hit /report so the function runs report generation.
# Schedule: every week Monday 9 AM IST (cron 1 = Monday; use 0=Sun, 2=Tue, ... 6=Sat)
SCHEDULE_CRON="${SCHEDULE_CRON:-0 9 * * 1}"
SCHEDULE_TIMEZONE="${SCHEDULE_TIMEZONE:-Asia/Kolkata}"
REPORT_URL="${FUNCTION_URL}/report"

echo -e "\n${GREEN}Setting up Cloud Scheduler (weekly at 9 AM)...${NC}"

if gcloud scheduler jobs describe ${CLOUD_FUNCTION_NAME}-scheduler --location=${GCP_REGION} &>/dev/null; then
    echo "Scheduler job exists, updating..."
    gcloud scheduler jobs update http ${CLOUD_FUNCTION_NAME}-scheduler \
        --location=${GCP_REGION} \
        --schedule="${SCHEDULE_CRON}" \
        --time-zone="${SCHEDULE_TIMEZONE}" \
        --uri="${REPORT_URL}" \
        --http-method=GET \
        --attempt-deadline=540s
else
    echo "Creating new scheduler job..."
    gcloud scheduler jobs create http ${CLOUD_FUNCTION_NAME}-scheduler \
        --location=${GCP_REGION} \
        --schedule="${SCHEDULE_CRON}" \
        --time-zone="${SCHEDULE_TIMEZONE}" \
        --uri="${REPORT_URL}" \
        --http-method=GET \
        --attempt-deadline=540s
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "\n${BLUE}Function Details:${NC}"
echo -e "  Name: ${CLOUD_FUNCTION_NAME}"
echo -e "  Region: ${GCP_REGION}"
echo -e "  URL: ${FUNCTION_URL}"
echo -e "  Person Lookup: ${FUNCTION_URL}/ or ${FUNCTION_URL}/person"
echo -e "  Schedule: Weekly Monday 9:00 AM ${SCHEDULE_TIMEZONE} (cron: ${SCHEDULE_CRON})"
echo -e "\n${BLUE}To test the report manually:${NC}"
echo -e "  curl ${REPORT_URL}"
echo -e "\n${BLUE}To view logs:${NC}"
echo -e "  gcloud functions logs read ${CLOUD_FUNCTION_NAME} --region=${GCP_REGION} --limit=50"
echo -e "\n${BLUE}To trigger scheduler manually:${NC}"
echo -e "  gcloud scheduler jobs run ${CLOUD_FUNCTION_NAME}-scheduler --location=${GCP_REGION}"
echo -e "\n${BLUE}Set APP_BASE_URL=${FUNCTION_URL} in .env and redeploy so Confluence embed uses this URL.${NC}"
