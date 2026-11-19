"""Command line: run the server and manage agent keys."""

from __future__ import annotations

import argparse
import re
import sys

from .config import Config
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-artifacts")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    agent = sub.add_parser("agent", help="manage agent keys")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    agent_sub.add_parser("add", help="create an agent and print its key").add_argument("name")
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

        uvicorn.run(create_app(config), host=args.host, port=args.port, proxy_headers=False)
        return 0

    store = Store(config.data_dir, max_total_bytes=config.max_total_bytes)
    if args.action == "add":
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.name):
            print("error: name may use letters, digits, '.', '_', '-'", file=sys.stderr)
            return 2
        if args.name in store.agents():
            print(f"error: agent {args.name!r} already exists", file=sys.stderr)
            return 1
        print(store.add_agent(args.name))
    elif args.action == "remove":
        if not store.remove_agent(args.name):
            print(f"error: no agent {args.name!r}", file=sys.stderr)
            return 1
    else:
        print("\n".join(store.agents()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
