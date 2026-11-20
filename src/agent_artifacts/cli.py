"""Command line: run the server and manage agent keys."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys

from .config import Config
from .store import NAME_RE, Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-artifacts")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    agent = sub.add_parser("agent", help="manage agent keys")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    add = agent_sub.add_parser("add", help="create an agent and print its key")
    add.add_argument("name")
    add.add_argument("--admin", action="store_true", help="may manage users and limits")
    admin = agent_sub.add_parser("admin", help="make an existing agent an admin")
    admin.add_argument("name")
    admin.add_argument("--revoke", action="store_true", help="take the admin role away")
    agent_sub.add_parser("remove", help="revoke an agent and delete its files").add_argument("name")
    agent_sub.add_parser("list", help="list agents")
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.command == "serve":
        import uvicorn

        from .web import create_app

        # No access log: the link in each request line is the access to the file.
        uvicorn.run(create_app(config), host=args.host, port=args.port, access_log=False)
        return 0

    store = Store(config.data_dir, max_total_bytes=config.max_total_bytes)
    if args.action == "add":
        if not re.fullmatch(NAME_RE, args.name):
            print(
                "error: name may use letters, digits, '.', '_', '-', and not start with '.'",
                file=sys.stderr,
            )
            return 2
        try:
            print(store.add_agent(args.name, admin=args.admin))
        except sqlite3.IntegrityError:
            print(f"error: agent {args.name!r} already exists", file=sys.stderr)
            return 1
    elif args.action == "admin":
        if not store.set_admin(args.name, not args.revoke):
            print(f"error: no agent {args.name!r}", file=sys.stderr)
            return 1
    elif args.action == "remove":
        if not store.remove_agent(args.name):
            print(f"error: no agent {args.name!r}", file=sys.stderr)
            return 1
    else:
        for a in store.agent_infos():
            print(a.name + (" (admin)" if a.admin else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
