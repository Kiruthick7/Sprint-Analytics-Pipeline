"""Microsoft Teams webhook client."""
import logging
import requests

logger = logging.getLogger(__name__)

TEAMS_MESSAGE_MAX_LEN = 28_000


def post_to_teams(webhook_url: str, message: str, recipient_name: str) -> bool:
    """Send message to Microsoft Teams via webhook."""
    if not webhook_url:
        logger.warning(f"No webhook URL provided for {recipient_name}")
        return False
    try:
        if len(message) > TEAMS_MESSAGE_MAX_LEN:
            message = message[:TEAMS_MESSAGE_MAX_LEN] + "\n\n... [Report truncated due to size limit]"
            logger.warning(f"Report truncated for {recipient_name} due to size limit")
        response = requests.post(webhook_url, json={"text": message}, timeout=30)
        if response.status_code == 200:
            logger.info(f"Successfully posted to Teams for {recipient_name}")
            return True
        logger.error(f"Failed to post to Teams for {recipient_name}: {response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error posting to Teams for {recipient_name}: {e}")
        return False
