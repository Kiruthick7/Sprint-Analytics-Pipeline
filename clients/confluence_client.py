"""Confluence API client for posting report pages."""
import logging
from urllib.parse import quote
import markdown
import requests

from config import CONFLUENCE_BASE_URL, CONFLUENCE_USER, CONFLUENCE_TOKEN, CONFLUENCE_SPACE_KEY

logger = logging.getLogger(__name__)


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
        api_url = f"{api_base}/wiki/rest/api/content"
        auth = (CONFLUENCE_USER, CONFLUENCE_TOKEN)
        headers = {"Content-Type": "application/json"}
        html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])

        # Search for existing page by title so we overwrite it instead of creating a new one each run
        search_url = f"{api_url}?type=page&spaceKey={quote(CONFLUENCE_SPACE_KEY)}&title={quote(title)}&expand=version"
        search_resp = requests.get(search_url, auth=auth, headers=headers, timeout=30)
        if search_resp.status_code == 200:
            data = search_resp.json()
            results = data.get("results") or []
            if results:
                existing = results[0]
                page_id = existing["id"]
                version = existing.get("version", {})
                current_version = version.get("number")
                if current_version is None:
                    get_page = requests.get(f"{api_url}/{page_id}?expand=version", auth=auth, headers=headers, timeout=30)
                    current_version = get_page.json().get("version", {}).get("number", 1) if get_page.status_code == 200 else 1
                current_version = int(current_version)
                update_payload = {
                    "id": page_id,
                    "type": "page",
                    "title": title,
                    "space": {"key": CONFLUENCE_SPACE_KEY},
                    "body": {"storage": {"value": html_content, "representation": "storage"}},
                    "version": {"number": current_version + 1},
                }
                update_resp = requests.put(f"{api_url}/{page_id}", json=update_payload, auth=auth, headers=headers, timeout=30)
                if update_resp.status_code == 200:
                    page_url = f"{wiki_base}/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
                    return page_url
                logger.error(f"Confluence update failed: {update_resp.status_code} - {update_resp.text[:200]}")
                return None
            # No existing page: create below
        elif search_resp.status_code != 404:
            logger.warning(f"Confluence search returned {search_resp.status_code}, attempting create")

        payload = {
            "type": "page",
            "title": title,
            "space": {"key": CONFLUENCE_SPACE_KEY},
            "body": {"storage": {"value": html_content, "representation": "storage"}},
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
