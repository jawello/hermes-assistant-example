"""Send a TickTick date with no time as an all-day task.

TickTick stores a bare ``dueDate: "YYYY-MM-DD"`` as ``isAllDay: false`` at 00:00.
Text rules in the skill and in memory failed three times, because background
review can rewrite them, so this corrects the call itself.

``isAllDay: false`` sent explicitly is left alone: it is how a real midnight
appointment stays possible. Anything unparseable passes through unchanged, since
a task at a wrong 00:00 is better than a lost task.
"""
import logging
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TOOLS = re.compile(r"^mcp_+ticktick_+(create_task|update_task|batch_add_tasks|batch_update_tasks)$")
_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEFAULT_ZONE = "Asia/Nicosia"


def _is_bare(value):
    return isinstance(value, str) and bool(_BARE_DATE.match(value))


def _local_day(value, zone):
    """The local day ``value`` names, or None when it carries a real clock time."""
    if _is_bare(value):
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value)
    local = parsed.astimezone(zone) if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return local.date() if local.time() == time(0) else None


def _local_midnight(day, zone):
    return datetime.combine(day, time(0), zone).strftime("%Y-%m-%dT%H:%M:%S%z")


def _normalised(task):
    """The all-day version of ``task``, or None when it should be left alone."""
    if task.get("isAllDay") is False:
        return None
    start, due = task.get("startDate"), task.get("dueDate")
    dates = [value for value in (start, due) if value]
    if not dates:
        return None
    if task.get("isAllDay") is True and not any(_is_bare(value) for value in dates):
        return None
    zone_name = task.get("timeZone") or _DEFAULT_ZONE
    zone = ZoneInfo(zone_name)
    days = [_local_day(value, zone) for value in dates]
    if None in days:
        return None
    return {
        **task,
        "isAllDay": True,
        "startDate": _local_midnight(days[0], zone),
        "dueDate": _local_midnight(days[-1], zone),
        "timeZone": zone_name,
    }


def _fixed(task):
    """Like ``_normalised``, but malformed input means no change rather than an error."""
    try:
        result = _normalised(task)
    except Exception as exc:
        logger.warning("ticktick_untimed: left a task unchanged: %s", exc)
        return None
    if result is not None:
        logger.info("ticktick_untimed: sent as all-day: %s", task.get("title") or task.get("id"))
    return result


def on_pre_tool_call(tool_name, args, **_):
    if not _TOOLS.match(tool_name or "") or not isinstance(args, dict):
        return None
    if isinstance(args.get("task"), dict):
        task = _fixed(args["task"])
        return {"action": "modify", "args": {"task": task}} if task else None
    if isinstance(args.get("tasks"), list):
        fixed = [_fixed(task) if isinstance(task, dict) else None for task in args["tasks"]]
        if not any(fixed):
            return None
        tasks = [new or old for new, old in zip(fixed, args["tasks"])]
        return {"action": "modify", "args": {"tasks": tasks}}
    return None


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
