"""``gridiron-admin`` — grant or revoke the admin role for a user.

    gridiron-admin promote you@example.com
    gridiron-admin demote  someone@example.com
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from gridiron.db.models import User
from gridiron.db.session import session_scope


def _set_role(email: str, role: str) -> int:
    email_norm = (email or "").strip().lower()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email_norm))
        if user is None:
            print(f"No user with email {email!r}", file=sys.stderr)
            return 1
        user.role = role
        print(f"{email_norm} is now '{role}'")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gridiron-admin", description="Manage user roles.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("promote", help="grant admin").add_argument("email")
    sub.add_parser("demote", help="revoke admin").add_argument("email")
    args = parser.parse_args(argv)
    return _set_role(args.email, "admin" if args.command == "promote" else "user")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
