# Use official Python runtime as base image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (packages + root config)
COPY main.py config.py team_config.py .
COPY jira_api/ jira_api/
COPY report/ report/
COPY clients/ clients/
COPY person/ person/
COPY static/ static/

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

CMD exec functions-framework --target=generate_and_send_report --port=$PORT
