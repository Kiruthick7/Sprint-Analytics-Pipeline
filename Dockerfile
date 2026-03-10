# Use official Python runtime as base image
FROM python:3.11-slim

WORKDIR /app

# Install build dependencies for Python packages (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (pinned versions for stability)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (packages + root config)
COPY main.py config.py team_config.py .
COPY jira_api/ jira_api/
COPY report/ report/
COPY clients/ clients/
COPY person/ person/
COPY utils/ utils/
COPY static/ static/

# Environment configuration
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Run the HTTP function server
# - Serves /report (generate report), /person (Developer lookup), /api/* (lookup APIs)
# - Uses Confluence REST API v2, consolidated board caching, optimized error handling
CMD exec functions-framework --target=generate_and_send_report --port=$PORT
