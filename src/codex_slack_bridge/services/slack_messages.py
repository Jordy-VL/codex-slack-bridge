"""Slack-native rendering of stored standard Markdown replies."""


def message_body(owner_label: str, answer: str) -> dict:
    body = owner_label + " — discussion pilot\n\n" + answer
    if len(body) <= 12000:
        # Slack translates standard Markdown into rich blocks and accessible text.
        # markdown_text cannot be combined with text or blocks.
        return {"markdown_text": body}
    # Preserve the existing 35k answer contract without truncation or extra posts.
    # Slack's native Markdown surface has a lower, cumulative 12k character limit.
    return {"text": "Long reply (plain text):\n\n" + body, "mrkdwn": False}
