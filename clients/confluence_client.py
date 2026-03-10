"""Confluence API client for posting report pages."""
import logging
import re
from urllib.parse import quote
import markdown
import requests

from config import CONFLUENCE_BASE_URL, CONFLUENCE_USER, CONFLUENCE_TOKEN, CONFLUENCE_SPACE_KEY

logger = logging.getLogger(__name__)


def _add_table_styles(html_content: str) -> str:
    """Wrap tables in constrained divs to prevent full-width stretching in Confluence cloud editor view mode.

    Applies container-based width constraints which are more reliable in Confluence Cloud's
    rendering engine than table-level inline styles. The overflow-x ensures wide tables
    remain scrollable rather than forcing text wrap.
    """
    def wrap_table(match):
        table = match.group(0)
        # Add inline styles to table element for additional control
        styled_table = table.replace('<table>', '<table style="width: auto; table-layout: auto;">')
        # Wrap in div with max-width constraint
        return f'<div style="max-width: 800px; overflow-x: auto; margin: 1em 0;">{styled_table}</div>'

    html_content = re.sub(r'<table>.*?</table>', wrap_table, html_content, flags=re.DOTALL)
    return html_content


def _get_space_id(space_key: str, auth: tuple, api_base: str) -> str | None:
    """Get numeric space ID from space key for v2 API."""
    spaces_url = f"{api_base}/wiki/api/v2/spaces?keys={quote(space_key)}"
    headers = {"Accept": "application/json"}
    try:
        resp = requests.get(spaces_url, auth=auth, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0]["id"]
        logger.error(f"Failed to get space ID for key '{space_key}': {resp.status_code}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting space ID: {e}")
        return None


def post_to_confluence(title: str, content: str) -> str | None:
    """Update existing Confluence page by title if found, otherwise create. Returns page URL."""
    if not all([CONFLUENCE_BASE_URL, CONFLUENCE_USER, CONFLUENCE_TOKEN, CONFLUENCE_SPACE_KEY]):
        logger.warning("Confluence credentials not configured - skipping Confluence posting")
        return None

    if CONFLUENCE_SPACE_KEY.startswith("~"):
        logger.warning(f"Using personal Confluence space ({CONFLUENCE_SPACE_KEY}). Consider using a team space key.")

    try:
        wiki_base = CONFLUENCE_BASE_URL.rstrip("/")
        api_base = wiki_base.replace("/wiki", "")
        auth = (CONFLUENCE_USER, CONFLUENCE_TOKEN)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])
        html_content = _add_table_styles(html_content)

        # Get numeric space ID for v2 API
        space_id = _get_space_id(CONFLUENCE_SPACE_KEY, auth, api_base)
        if not space_id:
            logger.error(f"Cannot post to Confluence: space '{CONFLUENCE_SPACE_KEY}' not found")
            return None

        # Search for existing page by title using v2 API
        api_url = f"{api_base}/wiki/api/v2/pages"
        search_url = f"{api_url}?space-id={space_id}&title={quote(title)}"
        search_resp = requests.get(search_url, auth=auth, headers=headers, timeout=30)
        
        if search_resp.status_code == 200:
            data = search_resp.json()
            results = data.get("results", [])
            if results:
                existing = results[0]
                page_id = existing["id"]
                current_version = existing.get("version", {}).get("number", 1)
                
                # Update existing page using v2 API
                update_payload = {
                    "id": page_id,
                    "status": "current",
                    "title": title,
                    "body": {
                        "representation": "storage",
                        "value": html_content
                    },
                    "version": {
                        "number": current_version + 1
                    }
                }
                update_resp = requests.put(f"{api_url}/{page_id}", json=update_payload, auth=auth, headers=headers, timeout=30)
                if update_resp.status_code == 200:
                    page_url = f"{wiki_base}/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
                    return page_url
                logger.error(f"Confluence update failed: {update_resp.status_code} - {update_resp.text[:200]}")
                return None
        elif search_resp.status_code != 404:
            logger.warning(f"Confluence search returned {search_resp.status_code}, attempting create")

        # Create new page using v2 API
        payload = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": html_content
            }
        }
        response = requests.post(api_url, json=payload, auth=auth, headers=headers, timeout=30)
        if response.status_code == 200:
            try:
                page_id = response.json()["id"]
                page_url = f"{wiki_base}/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
                return page_url
            except (KeyError, ValueError) as e:
                logger.error(f"Failed to parse Confluence response: {e}")
                return None

        error_msg = f"Status {response.status_code}"
        try:
            if response.content:
                error_msg += f" - {response.json()}"
        except Exception:
            error_msg += f" - {response.text[:200]}"
        logger.error(f"Failed to post to Confluence: {error_msg}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error posting to Confluence: {e}")
        return None
    except Exception as e:
        logger.error(f"Error posting to Confluence: {e}", exc_info=True)
        return None
