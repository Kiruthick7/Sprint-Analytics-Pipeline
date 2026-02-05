#!/bin/bash

# Deployment script for Jira Daily Report Cloud Function
# This script deploys the function and sets up Cloud Scheduler

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
    --set-env-vars="JIRA_USER_NAME=${JIRA_USER_NAME},JIRA_TOKEN=${JIRA_TOKEN},TEAMS_DG_EXP_WEBHOOK=${TEAMS_DG_EXP_WEBHOOK},TEAMS_DG_CORE_WEBHOOK=${TEAMS_DG_CORE_WEBHOOK},TEAMS_DG_FULFILL_WEBHOOK=${TEAMS_DG_FULFILL_WEBHOOK},TEAMS_WEBHOOK=${TEAMS_WEBHOOK}"

# Get the Cloud Function URL
FUNCTION_URL=$(gcloud functions describe ${CLOUD_FUNCTION_NAME} \
    --region=${GCP_REGION} \
    --format='value(serviceConfig.uri)')

echo -e "\n${GREEN}Cloud Function deployed successfully!${NC}"
echo -e "Function URL: ${FUNCTION_URL}"

# Create or update Cloud Scheduler job
echo -e "\n${GREEN}Setting up Cloud Scheduler (daily at 9 AM)...${NC}"

# Check if scheduler job exists
if gcloud scheduler jobs describe ${CLOUD_FUNCTION_NAME}-scheduler --location=${GCP_REGION} &>/dev/null; then
    echo "Scheduler job exists, updating..."
    gcloud scheduler jobs update http ${CLOUD_FUNCTION_NAME}-scheduler \
        --location=${GCP_REGION} \
        --schedule="0 9 * * *" \
        --time-zone="America/Chicago" \
        --uri="${FUNCTION_URL}" \
        --http-method=GET \
        --attempt-deadline=540s
else
    echo "Creating new scheduler job..."
    gcloud scheduler jobs create http ${CLOUD_FUNCTION_NAME}-scheduler \
        --location=${GCP_REGION} \
        --schedule="0 9 * * *" \
        --time-zone="America/Chicago" \
        --uri="${FUNCTION_URL}" \
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
echo -e "  Schedule: Daily at 9:00 AM (America/Chicago)"
echo -e "\n${BLUE}To test the function manually:${NC}"
echo -e "  curl ${FUNCTION_URL}"
echo -e "\n${BLUE}To view logs:${NC}"
echo -e "  gcloud functions logs read ${CLOUD_FUNCTION_NAME} --region=${GCP_REGION} --limit=50"
echo -e "\n${BLUE}To trigger scheduler manually:${NC}"
echo -e "  gcloud scheduler jobs run ${CLOUD_FUNCTION_NAME}-scheduler --location=${GCP_REGION}"
