#!/bin/bash

# Deploy Jira Daily Report as Cloud Function + Cloud Scheduler (Monday and Thursday 9 AM IST).
# Features: Confluence API v2, consolidated board caching, comprehensive validation, performance logging.
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
echo -e "\n${GREEN}Validating required environment variables...${NC}"

# GCP variables (required)
required_vars=("GCP_PROJECT_ID" "GCP_REGION" "CLOUD_FUNCTION_NAME")
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: $var must be set in .env${NC}"
        exit 1
    fi
done

# Jira variables (required)
jira_vars=("JIRA_USER_NAME" "JIRA_TOKEN" "JIRA_SERVER")
for var in "${jira_vars[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}Error: $var must be set in .env (required for Jira API)${NC}"
        exit 1
    fi
done

# Teams webhook (required for notifications)
if [ -z "$TEAMS_WEBHOOK" ]; then
    echo -e "${RED}Error: TEAMS_WEBHOOK must be set in .env (required for notifications)${NC}"
    exit 1
fi

# Confluence variables (optional but recommended)
if [ -z "$CONFLUENCE_BASE_URL" ] || [ -z "$CONFLUENCE_USER" ] || [ -z "$CONFLUENCE_TOKEN" ] || [ -z "$CONFLUENCE_SPACE_KEY" ]; then
    echo -e "${BLUE}Warning: Some Confluence variables not set. Reports will not be posted to Confluence.${NC}"
fi

# LLM provider (optional but recommended for comment summaries)
if [ -z "$LLM_PROVIDER" ] && [ -z "$GROQ_API_KEY" ] && [ -z "$GEMINI_API_KEY" ]; then
    echo -e "${BLUE}Warning: No LLM provider configured. Comment summaries will be skipped.${NC}"
elif [ "$LLM_PROVIDER" = "groq" ] && [ -z "$GROQ_API_KEY" ]; then
    echo -e "${RED}Error: LLM_PROVIDER set to 'groq' but GROQ_API_KEY is not set${NC}"
    exit 1
elif [ "$LLM_PROVIDER" = "gemini" ] && [ -z "$GEMINI_API_KEY" ]; then
    echo -e "${RED}Error: LLM_PROVIDER set to 'gemini' but GEMINI_API_KEY is not set${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All required variables validated${NC}"

# Set GCP project
echo -e "\n${GREEN}Setting GCP project...${NC}"
gcloud config set project $GCP_PROJECT_ID

# Enable required APIs
echo -e "\n${GREEN}Enabling required GCP APIs...${NC}"
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
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
# Schedule: Daily Mon-Sat 7 AM IST (matches GitHub Actions workflow)
SCHEDULE_CRON="${SCHEDULE_CRON:-30 1 * * 1-6}"
SCHEDULE_TIMEZONE="${SCHEDULE_TIMEZONE:-Asia/Kolkata}"
REPORT_URL="${FUNCTION_URL}/report"

echo -e "\n${GREEN}Setting up Cloud Scheduler (daily Mon-Sat at 7 AM IST)...${NC}"

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
echo -e "  Schedule: Daily Mon-Sat 7:00 AM ${SCHEDULE_TIMEZONE} (cron: ${SCHEDULE_CRON})"
echo -e "\n${BLUE}To test the report manually:${NC}"
echo -e "  curl ${REPORT_URL}"
echo -e "\n${BLUE}To view logs:${NC}"
echo -e "  gcloud functions logs read ${CLOUD_FUNCTION_NAME} --region=${GCP_REGION} --limit=50"
echo -e "\n${BLUE}To trigger scheduler manually:${NC}"
echo -e "  gcloud scheduler jobs run ${CLOUD_FUNCTION_NAME}-scheduler --location=${GCP_REGION}"
echo -e "\n${BLUE}Set APP_BASE_URL=${FUNCTION_URL} in .env and redeploy so Confluence embed uses this URL.${NC}"
