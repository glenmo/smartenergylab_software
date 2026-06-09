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

from app import app
from extensions import db
from models import User


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

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
