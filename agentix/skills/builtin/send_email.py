"""
Built-in Skill: send_email
Sends emails via SMTP — same credentials as the EmailChannel.

Required env vars:
  EMAIL_SMTP_HOST      — e.g. smtp.gmail.com
  EMAIL_SMTP_PORT      — default 587
  EMAIL_SMTP_USER      — sender address
  EMAIL_SMTP_PASSWORD  — SMTP password / app password
  EMAIL_SMTP_USE_TLS   — true (default) / false
  EMAIL_DEFAULT_TO     — fallback recipient when `to` is not provided
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

INSTRUCTIONS = """
## Email Skill
You can send emails using the `send_email` tool.

- Use `send_email` to deliver reports, digests, or alerts by email.
- Provide a clear `subject` line and well-structured `body`.
- Set `to` to a recipient address or rely on EMAIL_DEFAULT_TO env var.
- Plain text only by default; set content_type to "html" for HTML emails.
""".strip()


def _send_email(
    subject: str,
    body: str,
    to: str = "",
    content_type: str = "plain",
) -> dict:
    """Send an email via SMTP."""
    smtp_host = os.environ.get("EMAIL_SMTP_HOST", "")
    smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
    smtp_user = os.environ.get("EMAIL_SMTP_USER", "")
    smtp_pass = os.environ.get("EMAIL_SMTP_PASSWORD", "")
    use_tls = os.environ.get("EMAIL_SMTP_USE_TLS", "true").lower() == "true"
    default_to = os.environ.get("EMAIL_DEFAULT_TO", "")
    recipient = to or default_to

    if not smtp_host:
        return {"ok": False, "error": "EMAIL_SMTP_HOST env var not set"}
    if not smtp_user:
        return {"ok": False, "error": "EMAIL_SMTP_USER env var not set"}
    if not recipient:
        return {"ok": False, "error": "No recipient: provide `to` or set EMAIL_DEFAULT_TO"}

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, content_type))

    try:
        if use_tls:
            srv = smtplib.SMTP(smtp_host, smtp_port)
            srv.starttls()
        else:
            srv = smtplib.SMTP_SSL(smtp_host, smtp_port)
        srv.login(smtp_user, smtp_pass)
        srv.sendmail(smtp_user, [recipient], msg.as_string())
        srv.quit()
        return {"ok": True, "to": recipient, "subject": subject}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = {
    "send_email": _send_email,
}

TOOL_SCHEMAS = [
    {
        "name": "send_email",
        "description": (
            "Send an email via SMTP. "
            "Reads credentials from EMAIL_SMTP_* env vars. "
            "Recipient defaults to EMAIL_DEFAULT_TO if `to` is omitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient email address. Uses EMAIL_DEFAULT_TO if omitted.",
                    "default": "",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME content type: 'plain' (default) or 'html'",
                    "default": "plain",
                },
            },
            "required": ["subject", "body"],
        },
    },
]
