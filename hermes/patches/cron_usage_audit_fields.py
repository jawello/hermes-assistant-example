#!/usr/bin/env python3
"""Write what a cron fire cost, and why, into cron/usage_audit.jsonl.

**Why.** Upstream writes one line per fire with job_id, token totals,
silence, model and duration. That is not enough to judge a change like the
tool search floor (tool_search_min_tokens.py): job_id changes on every deploy,
because the entrypoint removes and recreates the jobs, and the line says
nothing about API calls, cache reads or the tools the model saw and used.
agent.log has some of it, but it rotates after 15 MB.

**The fix.** Every line also carries `job_name`, `api_calls`,
`cache_read_tokens`, `tool_calls` ({name: count}), `tool_errors` (tool results
that are a JSON object with a non-empty `error`) and `tool_surface` (bridged or not,
the schema tokens the model got per call, the weight of the deferrable tools,
the floor in force). Each field is computed on its own and becomes null when
that fails, so neither the line nor the job depends on it.

The patch fails the build if an anchor is gone or ambiguous, so an image
without the fields cannot ship quietly.
Design: design notes not published

Usage: `python cron_usage_audit_fields.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


MARK = "(patched in by personal-assistant's cron_usage_audit_fields.py)"

EXTRAS = f'''\
def _audit_field(compute):
    """One extra usage_audit field, or None if computing it fails {MARK}."""
    try:
        return compute()
    except Exception:
        return None


def _audit_tool_calls(messages) -> dict:
    """{{tool name: calls}} the model made, bridge tools included."""
    counts: dict = {{}}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
            name = function.get("name") if isinstance(function, dict) else getattr(function, "name", None)
            if name:
                counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _audit_tool_errors(messages) -> int:
    """Tool results that are a JSON object with a non-empty "error" (terminal
    answers "error": null on success)."""
    errors = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.lstrip().startswith("{{"):
            continue
        try:
            parsed = json.loads(content)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("error"):
            errors += 1
    return errors


def _audit_tool_surface(agent) -> Optional[dict]:
    """What the model saw: bridged or direct, and what that weighed."""
    if agent is None:
        return None
    from tools import tool_search as ts
    defs = list(getattr(agent, "tools", None) or [])
    names = {{(d.get("function") or {{}}).get("name") for d in defs if isinstance(d, dict)}}

    def deferrable_tokens():
        # The catalog the bridge itself reads: un-collapsed, the agent's toolsets.
        import model_tools
        raw = model_tools.get_tool_definitions(
            enabled_toolsets=getattr(agent, "enabled_toolsets", None),
            disabled_toolsets=getattr(agent, "disabled_toolsets", None),
            quiet_mode=True, skip_tool_search_assembly=True) or []
        config = ts.load_config_readonly()
        return ts.estimate_tokens_from_schemas(
            ts.classify_tools(raw, config.effective_defer_tools)[1])

    return {{
        "bridged": _audit_field(lambda: bool(names & set(ts.BRIDGE_TOOL_NAMES))),
        "schema_tokens": _audit_field(lambda: ts.estimate_tokens_from_schemas(defs)),
        "deferrable_tokens": _audit_field(deferrable_tokens),
        "min_deferred_tokens": _audit_field(
            lambda: ts.load_config_readonly().min_deferred_tokens),
    }}


def _fire_audit_extras(audit, result: dict) -> dict:
    """The fields a later analysis needs that upstream's line lacks."""
    messages = _audit_field(lambda: list(result.get("messages") or [])) or []
    return {{
        "job_name": _audit_field(lambda: audit.job.get("name")),
        "api_calls": _audit_field(lambda: result.get("api_calls")),
        "cache_read_tokens": _audit_field(lambda: result.get("cache_read_tokens")),
        "tool_calls": _audit_field(lambda: _audit_tool_calls(messages)),
        "tool_errors": _audit_field(lambda: _audit_tool_errors(messages)),
        "tool_surface": _audit_field(lambda: _audit_tool_surface(getattr(audit, "agent", None))),
    }}


'''

# (anchor, replacement) pairs, each anchor exactly once in the file.
EDITS = [
    (
        "class _FireAudit:\n",
        EXTRAS + "class _FireAudit:\n",
    ),
    (
        "    def __init__(self, job: dict, job_id: str, model: str):\n"
        "        self.job, self.job_id, self.model = job, job_id, model\n",
        "    def __init__(self, job: dict, job_id: str, model: str, agent=None):\n"
        "        self.job, self.job_id, self.model = job, job_id, model\n"
        "        self.agent = agent  # read by _fire_audit_extras\n",
    ),
    (
        '            "error": error})\n',
        '            "error": error,\n'
        "            **_fire_audit_extras(self, result)})\n",
    ),
    (
        "        _audit = _FireAudit(job, job_id, model)\n",
        "        _audit = _FireAudit(job, job_id, model, agent=agent)\n",
    ),
]


def patched_scheduler(source: str) -> str:
    """`cron/scheduler.py` whose usage_audit lines carry the extra fields."""
    if MARK in source:
        missing = [new for _, new in EDITS if new not in source]
        if missing:
            raise PatchError("cron/scheduler.py is only partly patched; re-check this patch")
        return source
    for anchor, replacement in EDITS:
        found = source.count(anchor)
        if found != 1:
            raise PatchError(
                f"expected one anchor in cron/scheduler.py, found {found}: "
                f"{anchor.splitlines()[0].strip()!r} — upstream changed the "
                "usage audit; re-check this patch"
            )
        source = source.replace(anchor, replacement, 1)
    return source


def main(argv: list[str]) -> int:
    target = Path(argv[1]) / "cron" / "scheduler.py"
    source = target.read_text()
    patched = patched_scheduler(source)
    if patched == source:
        print(f"{target}: already patched")
    else:
        target.write_text(patched)
        print(f"{target}: usage_audit lines now carry job_name, calls and tool surface")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
