"""
Built-in Skill: email-composer
Composes and sends emails via SMTP. Sending requires SMTP env vars.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
## Email Composer Skill
You can compose and send emails using these tools:
- `email_compose`: Draft an email (subject + body). Returns the draft for review.
- `email_send`: Send an email to a recipient. Requires SMTP configuration in environment.

When composing emails, be professional and concise. Always confirm the recipient before sending.

Required environment variables for sending:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
""".strip()


def _email_compose(to: str, subject: str, body: str, html: bool = False) -> dict:
    """Draft an email. Does NOT send — returns the draft for inspection."""
    return {
        "draft": True,
        "to": to,
        "subject": subject,
        "body": body,
        "format": "html" if html else "plain",
    }


def _email_send(to: str, subject: str, body: str, html: bool = False) -> dict:
    """Send an email via SMTP. Requires SMTP_* environment variables."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not smtp_host:
        return {"sent": False, "error": "SMTP_HOST not configured"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to
        mime_type = "html" if html else "plain"
        msg.attach(MIMEText(body, mime_type))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [to], msg.as_string())

        logger.info("Email sent to %s: %s", to, subject)
        return {"sent": True, "to": to, "subject": subject}

    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        return {"sent": False, "error": str(e)}


TOOLS = {
    "email_compose": _email_compose,
    "email_send": _email_send,
}

TOOL_SCHEMAS = [
    {
        "name": "email_compose",
        "description": "Compose an email draft. Returns the draft without sending it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body text"},
                "html": {"type": "boolean", "description": "Whether to send as HTML (default: false)", "default": False},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "email_send",
        "description": "Send an email to a recipient via SMTP. Requires SMTP environment variables to be configured.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body text"},
                "html": {"type": "boolean", "description": "Whether body is HTML (default: false)", "default": False},
            },
            "required": ["to", "subject", "body"],
        },
    },
]
