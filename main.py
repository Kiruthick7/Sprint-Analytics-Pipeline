"""Daily Jira report: generate team reports, post to Confluence, notify Teams; plus Individual Person lookup API and UI."""
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import functions_framework

# Load config first (env + Jira client)
import config  # noqa: F401 - ensure config is loaded before other imports that use it
from config import VELOCITY_DASHBOARD_URL
from team_config import TEAMS_CONFIG, REPORT_CHANNEL_WEBHOOK
from report.report_builder import (
    generate_team_report,
    build_consolidated_report,
    get_confluence_title,
    build_teams_message,
)
from clients.confluence_client import post_to_confluence
from clients.teams_client import post_to_teams
from person.person_lookup import get_all_developers, get_sprints_for_board, get_person_details

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if not REPORT_CHANNEL_WEBHOOK:
    logger.warning("TEAMS_WEBHOOK not set - report link will not be sent to Teams")
logger.info("Teams configured: %s", list(TEAMS_CONFIG.keys()))

# Optional: path to static HTML for person lookup (default next to main.py)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_response(data: Any, status: int = 200) -> tuple[str, int, dict]:
    return (json.dumps(data), status, {"Content-Type": "application/json; charset=utf-8"})


def _serve_person_html() -> tuple[str, int, dict]:
    """Serve the Developer lookup HTML page."""
    path = STATIC_DIR / "person.html"
    if not path.exists():
        return _json_response({"error": "Person lookup UI not found"}, 404)
    html = path.read_text(encoding="utf-8")
    return (html, 200, {"Content-Type": "text/html; charset=utf-8"})


def _get_request_path(request) -> str:
    """Get request path safely for routing (Flask has .path; some wrappers use environ['PATH_INFO'])."""
    path = getattr(request, "path", None)
    if not path:
        env = getattr(request, "environ", None) or {}
        path = env.get("PATH_INFO", "") or "/"
    path = (path or "/").strip().split("?")[0].rstrip("/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _get_request_arg(request, key: str, default: str = ""):
    """Get query arg safely (Flask request.args; fallback for minimal request objects)."""
    args = getattr(request, "args", None)
    if args is None:
        return default
    val = args.get(key, default)
    return val if val is not None else default


@functions_framework.http
def generate_and_send_report(request):
    """Route: / or /person -> person lookup UI; /api/developers, /api/sprints, /api/person -> JSON API; else run report."""
    path = _get_request_path(request)

    if path in ("/", "/person", "/index.html", "/person.html"):
        return _serve_person_html()

    if path == "/api/config":
        return _json_response({"velocity_dashboard_url": VELOCITY_DASHBOARD_URL or ""})

    if path == "/api/developers":
        try:
            data = get_all_developers()
            return _json_response(data)
        except Exception as e:
            logger.exception("api/developers")
            return _json_response({"error": str(e)}, 500)

    if path == "/api/sprints":
        board_id = _get_request_arg(request, "board_id")
        developer = _get_request_arg(request, "developer").strip()
        if not board_id:
            return _json_response({"error": "board_id required"}, 400)
        try:
            data = get_sprints_for_board(int(board_id), developer_name=developer or None)
            return _json_response(data)
        except Exception as e:
            logger.exception("api/sprints")
            return _json_response({"error": str(e)}, 500)

    if path == "/api/person":
        developer = _get_request_arg(request, "developer").strip()
        board_id = _get_request_arg(request, "board_id")
        sprint_id = _get_request_arg(request, "sprint_id")
        sprint_name = _get_request_arg(request, "sprint_name").strip()
        if not developer or not board_id:
            return _json_response({"error": "developer and board_id required"}, 400)
        if not sprint_id and not sprint_name:
            return _json_response({"error": "sprint_id or sprint_name required"}, 400)
        try:
            data = get_person_details(developer, int(board_id), sprint_id=int(sprint_id) if sprint_id else None, sprint_name=sprint_name or None)
            return _json_response(data)
        except Exception as e:
            logger.exception("api/person")
            return _json_response({"error": str(e)}, 500)

    # Run report only when explicitly requested (GET /report or POST for Cloud Scheduler)
    method = (getattr(request, "method", None) or "GET").upper()
    if path != "/report" and method != "POST":
        return _json_response({"error": "Not found", "path": path}, 404)

    logger.info("Starting Daily Jira Report Generation")
    results = {"timestamp": None, "teams": {}, "scrum_master": {"sent": False, "message": ""}}
    results["timestamp"] = datetime.now().isoformat()
    all_team_reports = []

    try:
        for idx, (team_name, team_config) in enumerate(TEAMS_CONFIG.items()):
            logger.info("%s\nProcessing team: %s\n%s", "=" * 80, team_name, "=" * 80)
            report, error, naming_warning, verification_violations = generate_team_report(team_name, team_config)

            if idx < len(TEAMS_CONFIG) - 1:
                logger.info("Waiting 15 seconds before next team to avoid rate limits...")
                time.sleep(15)

            if error:
                results["teams"][team_name] = {
                    "status": "error",
                    "message": error,
                    "sent_to_em": False,
                    "naming_warning": naming_warning,
                    "verification_violations": verification_violations,
                }
                continue

            all_team_reports.append({
                "team_name": team_name,
                "report": report,
                "naming_warning": naming_warning,
                "verification_violations": verification_violations,
            })
            results["teams"][team_name] = {
                "status": "success",
                "message": "",
                "sent_to_em": False,
                "naming_warning": naming_warning,
                "verification_violations": verification_violations,
            }

        if all_team_reports:
            logger.info("Building single Confluence page (all teams)")
            consolidated = build_consolidated_report(all_team_reports)
            confluence_url = post_to_confluence(get_confluence_title(), consolidated)
            teams_message = build_teams_message(all_team_reports, confluence_url, results)
            channel_sent = post_to_teams(REPORT_CHANNEL_WEBHOOK, teams_message, "Report channel") if REPORT_CHANNEL_WEBHOOK else False
            result_note = "Report link" if confluence_url else "Report link not created (Confluence error)"
            msg_suffix = f" {'sent' if channel_sent else 'not sent'} to shared channel"
            for team_data in all_team_reports:
                results["teams"][team_data["team_name"]]["sent_to_em"] = channel_sent
                results["teams"][team_data["team_name"]]["message"] = result_note + msg_suffix
            results["scrum_master"] = {
                "sent": channel_sent,
                "message": (result_note + msg_suffix) if REPORT_CHANNEL_WEBHOOK else "No report channel webhook configured",
                "teams_included": len(all_team_reports),
            }
        else:
            results["scrum_master"]["message"] = "No team reports available to consolidate" if REPORT_CHANNEL_WEBHOOK else "No webhook configured"
            for team_name in results["teams"]:
                if results["teams"][team_name].get("message") == "":
                    results["teams"][team_name]["message"] = "No report generated (error or no issues)"

        success_count = sum(1 for t in results["teams"].values() if t.get("sent_to_em"))
        total_teams = len(TEAMS_CONFIG)
        summary = f"\n✅ Report link sent to shared channel: {'Yes' if results['scrum_master']['sent'] else 'No'}\n"
        summary += f"✅ Teams included: {success_count}/{total_teams}\n"
        for team_name, team_result in results["teams"].items():
            summary += f"  • {team_name}: {team_result['message']}\n"
        logger.info("Report summary: %s", summary.replace("\n", " "))
        overall_success = success_count == total_teams and (results["scrum_master"]["sent"] or not REPORT_CHANNEL_WEBHOOK)
        return (summary, 200 if overall_success else 207)

    except Exception as e:
        error_msg = f"❌ Critical error in report generation: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return (error_msg, 500)


if __name__ == "__main__":
    class MockRequest:
        """Minimal request for local test; path='/report' runs the daily report."""

        def __init__(self) -> None:
            self.args: dict[str, Any] = {}
            self.headers: dict[str, str] = {}
            self.method: str = "GET"
            self.environ: dict[str, Any] = {}
            self.path: str = "/report"  # so local test runs report, not person UI

    logger.info("Running in local test mode")
    result, status = generate_and_send_report(MockRequest())  # type: ignore
    print(f"\nResult ({status}):\n{result}")
