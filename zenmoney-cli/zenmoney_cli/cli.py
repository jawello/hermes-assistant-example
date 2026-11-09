"""zenmoney — agent-friendly CLI for the Zen-Money v8 diff API."""
import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from zenmoney_cli import ops
from zenmoney_cli.api import ZenMoneyClient, ZenMoneyError
from zenmoney_cli.state import State


def state_dir() -> Path:
    if os.environ.get("ZENMONEY_STATE_DIR"):
        return Path(os.environ["ZENMONEY_STATE_DIR"])
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]) / "zenmoney"
    return Path.home() / ".zenmoney"


def parse_part(raw: str) -> tuple[str, str]:
    name, sep, amount = raw.partition("=")
    if not sep or not name.strip() or not amount.strip():
        raise SystemExit(f'bad part {raw!r}, expected "Категория=12.34"')
    return name.strip(), amount.strip()


def emit(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return code


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="zenmoney")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync")
    sub.add_parser("tags")
    f = sub.add_parser("find")
    f.add_argument("--date", required=True)
    f.add_argument("--amount", required=True)
    f.add_argument("--payee")
    f.add_argument("--window", type=int, default=3)
    s = sub.add_parser("show")
    s.add_argument("txid")
    sp = sub.add_parser("split")
    sp.add_argument("txid")
    sp.add_argument("parts", nargs="+")
    sp.add_argument("--comment")
    c = sub.add_parser("create")
    c.add_argument("--date", required=True)
    c.add_argument("--amount", required=True)
    c.add_argument("--payee", required=True)
    c.add_argument("--tag", required=True)
    c.add_argument("--account")
    c.add_argument("--comment")
    args = p.parse_args(argv)

    token = os.environ.get("ZENMONEY_TOKEN")
    if not token:
        return emit({"ok": False, "error":
                     "ZENMONEY_TOKEN is not set — ask the user for a Zen-Money "
                     "token (app → Настройки → Доступ по токену)"}, 1)

    try:
        client = ZenMoneyClient(token)
        state = State.load(state_dir())
        sync_info = ops.sync(client, state)
        if args.cmd == "sync":
            return emit({"ok": True, **sync_info})
        if args.cmd == "tags":
            return emit({"ok": True, "tags": ops.list_tags(state)})
        if args.cmd == "find":
            found = ops.find(state, args.date, Decimal(args.amount),
                             payee=args.payee, window=args.window)
            return emit({"ok": True, "candidates": found})
        if args.cmd == "show":
            tx = state.entities["transaction"].get(args.txid)
            if tx is None:
                return emit({"ok": False,
                             "error": f"no transaction {args.txid}"}, 1)
            view = dict(tx, tags=[ops.tag_path(state, t)
                                  for t in (tx.get("tag") or [])])
            return emit({"ok": True, "transaction": view})
        if args.cmd == "split":
            parts = [(n, Decimal(a))
                     for n, a in (parse_part(raw) for raw in args.parts)]
            return emit(ops.split(client, state, args.txid, parts,
                                  comment=args.comment))
        if args.cmd == "create":
            return emit(ops.create(client, state, args.date,
                                   Decimal(args.amount), args.payee, args.tag,
                                   account=args.account,
                                   comment=args.comment))
        raise AssertionError(args.cmd)
    except (ValueError, InvalidOperation, ZenMoneyError) as exc:
        return emit({"ok": False, "error": str(exc)}, 1)
    except Exception as exc:  # httpx errors etc — still JSON, still exit 1
        return emit({"ok": False,
                     "error": f"{type(exc).__name__}: {exc}"}, 1)


if __name__ == "__main__":
    sys.exit(main())
