"""Shared Jira utility functions used across multiple modules."""
import logging

logger = logging.getLogger(__name__)


def parse_story_point_value(raw) -> float:
    """Parse a story point value from API (number, string, or dict like {'value': 3})."""
    if raw is None:
        return 0.0
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("id")
    if raw is None:
        return 0.0
    try:
        v = float(raw)
        return v if v >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_story_points(issue, story_points_field: str | None = None) -> float:
    """Get story points from issue (board config or JIRA_STORY_POINTS_FIELD).

    Args:
        issue: Jira issue object
        story_points_field: Custom field ID (e.g., 'customfield_10016'). If None, uses JIRA_STORY_POINTS_FIELD from config.

    Returns:
        Story points as float, or 0.0 if not found or invalid
    """
    from config import JIRA_STORY_POINTS_FIELD

    field = story_points_field or JIRA_STORY_POINTS_FIELD
    if not field:
        return 0.0
    raw = getattr(issue.fields, field, None)
    if raw is not None:
        v = parse_story_point_value(raw)
        if v >= 0:
            return v
    raw_top = getattr(issue, "raw", None) or {}
    fields_dict = raw_top.get("fields") if isinstance(raw_top, dict) else {}
    if isinstance(fields_dict, dict):
        raw = fields_dict.get(field)
        if raw is not None:
            v = parse_story_point_value(raw)
            if v >= 0:
                return v
    return 0.0
