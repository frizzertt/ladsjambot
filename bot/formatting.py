"""Formatting for displayed Telegram names; stored names keep their @ for lookups."""
import html


def user_name_html(name: str) -> str:
    """Show a name as HTML text without creating @mentions, including in full names."""
    return html.escape(name.replace("@", ""))
