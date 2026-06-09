"""
Tiny SMTP helper for sending password-reset mail via Gmail's SMTP.

Why not Flask-Mail: this module needs ~30 lines and zero extension
boilerplate. The send_reset_email() call is the only thing the rest of
the app calls into.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger("portal.mail")


def _smtp_connect():
    cfg = current_app.config
    host = cfg["SMTP_HOST"]
    port = cfg["SMTP_PORT"]
    # STARTTLS path for Gmail's submission service on 587.
    ctx = ssl.create_default_context()
    s = smtplib.SMTP(host, port, timeout=20)
    s.ehlo()
    s.starttls(context=ctx)
    s.ehlo()
    s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
    return s


def send(to_email: str, subject: str, body_text: str, body_html: str | None = None):
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = cfg["MAIL_FROM"]
    msg["To"]      = to_email
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        with _smtp_connect() as s:
            s.send_message(msg)
        log.info(f"mail: sent '{subject}' to {to_email}")
    except Exception as e:
        # Don't leak SMTP error detail to the user — log it and re-raise as
        # a generic RuntimeError so the route can decide what to surface.
        log.error(f"mail: send to {to_email} failed: {e}")
        raise RuntimeError("mail send failed") from e


def send_alert(subject: str, body: str):
    """
    Dispatch an operational alert to LOGIN_ALERT_TO. Silently does
    nothing if that config key is unset, so dev runs without SMTP
    config don't blow up.
    """
    to = current_app.config.get("LOGIN_ALERT_TO")
    if not to:
        return
    try:
        send(to, f"[Smart Energy Lab] {subject}", body)
    except Exception:
        # Alerting must never break the request that triggered it.
        # Failure is already logged inside send().
        pass


def send_reset_email(to_email: str, reset_url: str):
    subject = "Reset your Smart Energy Lab password"
    text = (
        "Someone (hopefully you) asked to reset the password on your\n"
        "Smart Energy Lab account.\n\n"
        f"Reset link (valid for 1 hour):\n  {reset_url}\n\n"
        "If you did not request this, ignore this email — your password\n"
        "will not change.\n"
    )
    html = f"""\
<p>Someone (hopefully you) asked to reset the password on your Smart Energy Lab account.</p>
<p><a href="{reset_url}">Click here to reset your password</a> (valid for 1 hour).</p>
<p style="color:#666;font-size:0.9em">If you did not request this, ignore this email — your password will not change.</p>
"""
    send(to_email, subject, text, html)
