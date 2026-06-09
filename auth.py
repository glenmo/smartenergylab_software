"""
Authentication routes: login, logout, forgot-password, reset-password.

Sets the session cookie on the parent domain so it's shared across
*.smartenergylab.software subdomains — i.e. a single login covers the
portal page and every mirrored inverter dashboard.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_user, logout_user
from flask_wtf import FlaskForm
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Email, Length, EqualTo

from extensions import db, login_manager, limiter
from models import User, PasswordResetToken, LoginEvent
from mail import send_reset_email, send_alert

log = logging.getLogger("portal.auth")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------
class LoginForm(FlaskForm):
    email    = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])


class ForgotForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])


class ResetForm(FlaskForm):
    password = PasswordField("New password",
                             validators=[DataRequired(), Length(min=12, message="At least 12 characters.")])
    confirm  = PasswordField("Confirm new password",
                             validators=[DataRequired(), EqualTo("password", message="Passwords must match.")])


# ---------------------------------------------------------------------------
# Token signing — itsdangerous keeps the actual reset-token string short
# and tamper-evident. We also persist a hash so a token can only be used
# once (used_at flips on success).
# ---------------------------------------------------------------------------
def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="pw-reset")


def _make_token(user_email: str) -> str:
    return _serializer().dumps(user_email)


def _read_token(token: str, max_age: int) -> str | None:
    try:
        return _serializer().loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None


# ---------------------------------------------------------------------------
# Login event recording + burst-alert dispatch
# ---------------------------------------------------------------------------
def _record_login_event(email: str, success: bool, user: User | None):
    """Persist a row to login_events. Never raises."""
    try:
        evt = LoginEvent(
            email_attempted=email,
            success=success,
            ip_addr=(request.remote_addr or "")[:64],
            user_agent=(request.headers.get("User-Agent") or "")[:1024],
            user_id=user.id if user else None,
        )
        db.session.add(evt)
        db.session.commit()
    except Exception as e:
        log.warning(f"auth: failed to record login event: {e}")


def _maybe_alert_on_burst(email: str):
    """
    Send an email alert if the failure count for `email` or for this
    request's source IP has just crossed LOGIN_ALERT_THRESHOLD within
    LOGIN_ALERT_WINDOW. Respects LOGIN_ALERT_COOLDOWN so a sustained
    attack produces at most one email per cooldown period per target.
    """
    cfg = current_app.config
    if not cfg.get("LOGIN_ALERT_TO"):
        return

    threshold = cfg.get("LOGIN_ALERT_THRESHOLD", 5)
    window_s  = cfg.get("LOGIN_ALERT_WINDOW",   60 * 15)
    cooldown  = cfg.get("LOGIN_ALERT_COOLDOWN", 60 * 60)
    now       = datetime.now(timezone.utc)
    since     = now - timedelta(seconds=window_s)
    cooldown_since = now - timedelta(seconds=cooldown)

    ip = request.remote_addr or ""

    def _failures(filter_clause):
        return db.session.execute(
            db.select(db.func.count(LoginEvent.id))
              .filter(LoginEvent.success.is_(False))
              .filter(LoginEvent.ts >= since)
              .filter(filter_clause)
        ).scalar_one()

    def _has_recent_alert(filter_clause):
        # An alert "exists" as a sentinel row in login_events with
        # email_attempted prefixed by "_ALERT:". Cheap and avoids a
        # whole new alerts table.
        return db.session.execute(
            db.select(db.func.count(LoginEvent.id))
              .filter(LoginEvent.ts >= cooldown_since)
              .filter(filter_clause)
        ).scalar_one() > 0

    def _emit_alert(target_label, target_value, count):
        # Drop a sentinel row marking the alert.
        sentinel = LoginEvent(
            email_attempted=f"_ALERT:{target_label}:{target_value}",
            success=False,
            ip_addr=ip[:64],
            user_agent="(alert sentinel)",
        )
        db.session.add(sentinel)
        db.session.commit()
        send_alert(
            f"{count} failed logins targeting {target_label} {target_value}",
            f"{count} failed login attempts within the last "
            f"{window_s // 60} minutes targeted {target_label} = {target_value!r}.\n\n"
            f"Most recent IP: {ip}\n"
            f"Threshold:      {threshold}\n"
            f"Window:         {window_s} s\n\n"
            f"Investigate at burgan with:\n"
            f"  cd /opt/burgan-portal\n"
            f"  sudo -u burgan-portal env PORTAL_CONFIG=/etc/burgan-portal/config.py \\\n"
            f"      venv/bin/python manage.py failed-logins --limit 30\n"
        )

    # Email-keyed burst
    if not _has_recent_alert(LoginEvent.email_attempted == f"_ALERT:email:{email}"):
        count = _failures(LoginEvent.email_attempted == email)
        if count >= threshold:
            _emit_alert("email", email, count)
            return

    # IP-keyed burst
    if ip and not _has_recent_alert(LoginEvent.email_attempted == f"_ALERT:ip:{ip}"):
        count = _failures(LoginEvent.ip_addr == ip)
        if count >= threshold:
            _emit_alert("ip", ip, count)


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["RATE_LIMIT_LOGIN"], methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("portal.index"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.execute(
            db.select(User).filter_by(email=email)
        ).scalar_one_or_none()
        if user and user.check_password(form.password.data):
            login_user(user, remember=True)
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            _record_login_event(email, success=True, user=user)
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("portal.index"))
        # Generic error — don't leak whether the email exists.
        flash("Invalid email or password.", "error")
        log.info(f"auth: failed login for {email}")
        _record_login_event(email, success=False, user=user)
        _maybe_alert_on_burst(email)

    return render_template("login.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/forgot", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["RATE_LIMIT_FORGOT"], methods=["POST"])
def forgot():
    form = ForgotForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.execute(
            db.select(User).filter_by(email=email)
        ).scalar_one_or_none()

        if user:
            ttl = current_app.config["RESET_TOKEN_TTL_SECONDS"]
            token = _make_token(user.email)
            # Persist a row so we can mark it used and prevent reuse.
            db.session.add(PasswordResetToken.new_for_user(user, token, ttl))
            db.session.commit()
            reset_url = current_app.config["PORTAL_BASE_URL"] + url_for(
                "auth.reset", token=token
            )
            try:
                send_reset_email(user.email, reset_url)
            except RuntimeError:
                # Mail failure — we deliberately don't tell the user.
                # Logged in mail.py.
                pass
        else:
            log.info(f"auth: forgot-password for unknown email {email}")

        # Always the same response — don't reveal existence.
        flash("If that email is registered, we've sent a reset link.", "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot.html", form=form)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    ttl   = current_app.config["RESET_TOKEN_TTL_SECONDS"]
    email = _read_token(token, max_age=ttl)
    if email is None:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot"))

    db_token = db.session.execute(
        db.select(PasswordResetToken).filter_by(token=token)
    ).scalar_one_or_none()
    if db_token is None or not db_token.is_valid():
        flash("This reset link has already been used or has expired.", "error")
        return redirect(url_for("auth.forgot"))

    form = ResetForm()
    if form.validate_on_submit():
        user = db.session.execute(
            db.select(User).filter_by(email=email)
        ).scalar_one_or_none()
        if user is None:
            flash("Account no longer exists.", "error")
            return redirect(url_for("auth.login"))

        user.set_password(form.password.data)
        db_token.used_at = datetime.now(timezone.utc)
        db.session.commit()
        log.info(f"auth: password reset for {user.email}")
        flash("Password updated. Please sign in.", "info")
        return redirect(url_for("auth.login"))

    return render_template("reset.html", form=form, token=token)
