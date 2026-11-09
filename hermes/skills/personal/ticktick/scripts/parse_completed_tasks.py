#!/usr/bin/env python3
"""Parse a persisted `list_completed_tasks_by_date` payload.

The MCP transport persists the ~400 KB concatenated-JSON-object response to a
file and returns only a short preview. Pass that file path as argv[1] (or pipe
the raw payload on stdin) and this prints one line per completed task:

    completedTime | projectId | title

sorted by completion time, preceded by a `# N completed tasks` count. See
ticktick rule R10.

Usage:
    python3 scripts/parse_completed_tasks.py /tmp/hermes-results/call_xxx.txt
    cat payload.txt | python3 scripts/parse_completed_tasks.py
"""
import json
import sys


def load_payload() -> str:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            return fh.read()
    return sys.stdin.read()


def main() -> None:
    s = load_payload()

    # Unwrap the transport envelope {"result": "...."} if present.
    try:
        outer = json.loads(s)
        if isinstance(outer, dict) and isinstance(outer.get("result"), str):
            s = outer["result"]
    except Exception:
        pass

    dec = json.JSONDecoder()
    i, n, rows = 0, 0, []
    while i < len(s):
        while i < len(s) and s[i] in " \n\r\t":
            i += 1
        if i >= len(s):
            break
        obj, i = dec.raw_decode(s, i)
        n += 1
        rows.append((obj.get("completedTime"), obj.get("projectId"), obj.get("title")))

    print(f"# {n} completed tasks")
    for ct, pid, title in sorted(rows, key=lambda r: (r[0] or "", r[2] or "")):
        print(f"{ct} | {pid} | {title}")


if __name__ == "__main__":
    main()