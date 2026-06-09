#!/usr/bin/env python3
"""
Smart Energy Lab portal — management CLI.

Used on the burgan host to create the first user (no self-serve
registration), reset a password, list or delete users.

Examples (run inside the project venv with PORTAL_CONFIG set):

    python manage.py create-user glen@example.com
    python manage.py set-password glen@example.com
    python manage.py list-users
    python manage.py delete-user glen@example.com
"""

import argparse
import getpass
import sys
from datetime import datetime, timedelta, timezone

from app import app
from extensions import db
from models import User, LoginEvent


def _get_user(email):
    return db.session.execute(
        db.select(User).filter_by(email=email.lower())
    ).scalar_one_or_none()


def cmd_create_user(args):
    email = args.email.strip().lower()
    with app.app_context():
        if _get_user(email):
            print(f"User {email!r} already exists.", file=sys.stderr)
            return 1
        pw1 = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm:  ")
        if pw1 != pw2:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        if len(pw1) < 12:
            print("Password must be at least 12 characters.", file=sys.stderr)
            return 1
        user = User(email=email)
        user.set_password(pw1)
        db.session.add(user)
        db.session.commit()
        print(f"Created user {email!r}.")
    return 0


def cmd_set_password(args):
    email = args.email.strip().lower()
    with app.app_context():
        user = _get_user(email)
        if user is None:
            print(f"No user with email {email!r}.", file=sys.stderr)
            return 1
        pw1 = getpass.getpass("New password: ")
        pw2 = getpass.getpass("Confirm:      ")
        if pw1 != pw2:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        if len(pw1) < 12:
            print("Password must be at least 12 characters.", file=sys.stderr)
            return 1
        user.set_password(pw1)
        db.session.commit()
        print(f"Updated password for {email!r}.")
    return 0


def cmd_list_users(_args):
    with app.app_context():
        users = db.session.execute(db.select(User).order_by(User.email)).scalars().all()
        if not users:
            print("(no users)")
            return 0
        for u in users:
            ll = u.last_login.isoformat() if u.last_login else "(never)"
            print(f"{u.id:>4}  {u.email:<40}  last_login={ll}")
    return 0


def cmd_delete_user(args):
    email = args.email.strip().lower()
    with app.app_context():
        user = _get_user(email)
        if user is None:
            print(f"No user with email {email!r}.", file=sys.stderr)
            return 1
        if not args.yes:
            confirm = input(f"Delete {email!r}? Type 'yes' to confirm: ")
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return 1
        db.session.delete(user)
        db.session.commit()
        print(f"Deleted user {email!r}.")
    return 0


def _fmt_ts(ts):
    if ts is None:
        return "(none)"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def cmd_recent_logins(args):
    with app.app_context():
        q = db.select(LoginEvent).filter(LoginEvent.success.is_(True))
        if args.email:
            q = q.filter(LoginEvent.email_attempted == args.email.strip().lower())
        # Hide the alert sentinel rows
        q = q.filter(~LoginEvent.email_attempted.like("_ALERT:%"))
        q = q.order_by(LoginEvent.ts.desc()).limit(args.limit)
        rows = db.session.execute(q).scalars().all()
        if not rows:
            print("(no successful logins recorded yet)")
            return 0
        print(f"{'when':<24}  {'email':<32}  {'ip':<16}  user-agent")
        print("-" * 100)
        for r in rows:
            ua = (r.user_agent or "").replace("\n", " ")[:60]
            print(f"{_fmt_ts(r.ts):<24}  {r.email_attempted:<32}  {(r.ip_addr or '-'):<16}  {ua}")
    return 0


def cmd_failed_logins(args):
    with app.app_context():
        since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
        q = (
            db.select(LoginEvent)
              .filter(LoginEvent.success.is_(False))
              .filter(LoginEvent.ts >= since)
              .filter(~LoginEvent.email_attempted.like("_ALERT:%"))
              .order_by(LoginEvent.ts.desc())
              .limit(args.limit)
        )
        rows = db.session.execute(q).scalars().all()
        if not rows:
            print(f"(no failed logins in the last {args.since_hours} h)")
            return 0
        print(f"{'when':<24}  {'email tried':<32}  {'ip':<16}  user-agent")
        print("-" * 100)
        for r in rows:
            ua = (r.user_agent or "").replace("\n", " ")[:60]
            print(f"{_fmt_ts(r.ts):<24}  {r.email_attempted:<32}  {(r.ip_addr or '-'):<16}  {ua}")

        # Summary
        from sqlalchemy import func
        by_email = db.session.execute(
            db.select(LoginEvent.email_attempted, func.count(LoginEvent.id))
              .filter(LoginEvent.success.is_(False))
              .filter(LoginEvent.ts >= since)
              .filter(~LoginEvent.email_attempted.like("_ALERT:%"))
              .group_by(LoginEvent.email_attempted)
              .order_by(func.count(LoginEvent.id).desc())
              .limit(10)
        ).all()
        if by_email:
            print()
            print(f"top email targets, last {args.since_hours} h:")
            for email, n in by_email:
                print(f"  {n:>5}  {email}")

        by_ip = db.session.execute(
            db.select(LoginEvent.ip_addr, func.count(LoginEvent.id))
              .filter(LoginEvent.success.is_(False))
              .filter(LoginEvent.ts >= since)
              .filter(LoginEvent.ip_addr.isnot(None))
              .filter(~LoginEvent.email_attempted.like("_ALERT:%"))
              .group_by(LoginEvent.ip_addr)
              .order_by(func.count(LoginEvent.id).desc())
              .limit(10)
        ).all()
        if by_ip:
            print()
            print(f"top source IPs, last {args.since_hours} h:")
            for ip, n in by_ip:
                print(f"  {n:>5}  {ip}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Smart Energy Lab portal admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("create-user", help="Create a new portal user")
    p1.add_argument("email")
    p1.set_defaults(func=cmd_create_user)

    p2 = sub.add_parser("set-password", help="Reset a user's password (admin)")
    p2.add_argument("email")
    p2.set_defaults(func=cmd_set_password)

    p3 = sub.add_parser("list-users", help="List all users")
    p3.set_defaults(func=cmd_list_users)

    p4 = sub.add_parser("delete-user", help="Permanently delete a user")
    p4.add_argument("email")
    p4.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p4.set_defaults(func=cmd_delete_user)

    p5 = sub.add_parser("recent-logins",
                        help="Show recent successful logins (newest first)")
    p5.add_argument("--limit", type=int, default=20)
    p5.add_argument("--email", default=None, help="Filter to a single email")
    p5.set_defaults(func=cmd_recent_logins)

    p6 = sub.add_parser("failed-logins",
                        help="Show recent failed login attempts + top-N summary")
    p6.add_argument("--limit", type=int, default=30)
    p6.add_argument("--since-hours", type=int, default=24,
                    help="Only show failures from the last N hours (default 24)")
    p6.set_defaults(func=cmd_failed_logins)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
