"""A date with no time must reach TickTick as an all-day task, whatever the model sent."""
import copy
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "ticktick_untimed" / "__init__.py"
_spec = importlib.util.spec_from_file_location("ticktick_untimed", _PATH)
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

CREATE = "mcp__ticktick__create_task"
ALL_DAY_NOV_5 = {
    "isAllDay": True,
    "startDate": "2026-11-05T00:00:00+0200",
    "dueDate": "2026-11-05T00:00:00+0200",
    "timeZone": "Asia/Nicosia",
}


def hook(task, tool=CREATE):
    return plugin.on_pre_tool_call(tool, {"task": task}, task_id="t")


def test_bare_due_date_becomes_all_day():
    result = hook({"title": "x", "dueDate": "2026-11-05"})
    assert result == {"action": "modify", "args": {"task": {"title": "x", **ALL_DAY_NOV_5}}}


def test_local_midnight_without_the_flag_becomes_all_day():
    result = hook({"title": "x", "dueDate": "2026-11-05T00:00:00+0200"})
    assert result["args"]["task"] == {"title": "x", **ALL_DAY_NOV_5}


def test_midnight_written_in_utc_is_still_local_midnight():
    result = hook({"title": "x", "dueDate": "2026-11-04T22:00:00+0000"})
    assert result["args"]["task"] == {"title": "x", **ALL_DAY_NOV_5}


def test_naive_midnight_is_read_as_local():
    result = hook({"title": "x", "dueDate": "2026-11-05T00:00:00"})
    assert result["args"]["task"] == {"title": "x", **ALL_DAY_NOV_5}


def test_bare_start_date_alone_becomes_all_day():
    result = hook({"title": "x", "startDate": "2026-11-05"})
    assert result["args"]["task"] == {"title": "x", **ALL_DAY_NOV_5}


def test_bare_date_with_the_flag_already_set_gets_full_dates():
    result = hook({"title": "x", "isAllDay": True, "dueDate": "2026-11-05"})
    assert result["args"]["task"] == {"title": "x", **ALL_DAY_NOV_5}


def test_multi_day_range_keeps_both_days():
    task = hook({"startDate": "2026-11-05", "dueDate": "2026-11-07"})["args"]["task"]
    assert task["startDate"] == "2026-11-05T00:00:00+0200"
    assert task["dueDate"] == "2026-11-07T00:00:00+0200"


@pytest.mark.parametrize(
    "day,offset",
    # The clocks change after midnight, so midnight on the change day keeps the old offset.
    [("2026-03-29", "+0200"), ("2026-03-30", "+0300"), ("2026-10-25", "+0300"), ("2026-10-26", "+0200")],
)
def test_offset_follows_daylight_saving_for_that_date(day, offset):
    assert hook({"dueDate": day})["args"]["task"]["dueDate"] == f"{day}T00:00:00{offset}"


def test_the_tasks_own_time_zone_is_kept():
    task = hook({"dueDate": "2026-11-05", "timeZone": "Europe/Moscow"})["args"]["task"]
    assert task["dueDate"] == "2026-11-05T00:00:00+0300"
    assert task["timeZone"] == "Europe/Moscow"


@pytest.mark.parametrize(
    "task",
    [
        pytest.param({"dueDate": "2026-11-05T00:00:00+0200", "isAllDay": False}, id="explicit-false"),
        pytest.param({"dueDate": "2026-11-05", "isAllDay": False}, id="explicit-false-bare"),
        pytest.param({"dueDate": "2026-11-05T19:15:00+0200"}, id="clock-time"),
        pytest.param({"startDate": "2026-11-05T00:00:00+0200", "dueDate": "2026-11-05T02:00:00+0200"}, id="span"),
        pytest.param({"startDate": "2026-11-05T10:00:00+0200", "dueDate": "2026-11-05"}, id="start-has-time"),
        pytest.param({"dueDate": "2026-11-05T00:00:00+0000"}, id="utc-midnight-is-02-local"),
        pytest.param({"dueDate": "1970-01-01T00:00:00.000+0000"}, id="clear-date-sentinel"),
        pytest.param(dict(ALL_DAY_NOV_5), id="already-correct"),
        pytest.param({"title": "no dates"}, id="no-dates"),
        pytest.param({"dueDate": None, "startDate": None}, id="null-dates"),
    ],
)
def test_left_alone(task):
    assert hook(task) is None


@pytest.mark.parametrize(
    "task",
    [
        pytest.param({"dueDate": "not a date"}, id="garbage"),
        pytest.param({"dueDate": "2026-13-45"}, id="impossible-date"),
        pytest.param({"dueDate": 20261105}, id="not-a-string"),
        pytest.param({"dueDate": "2026-11-05", "timeZone": "Mars/Base"}, id="unknown-zone"),
        pytest.param("not a dict", id="task-not-a-dict"),
    ],
)
def test_malformed_input_passes_through(task):
    assert hook(task) is None


def test_update_task_is_covered():
    args = {"task_id": "abc", "task": {"id": "abc", "dueDate": "2026-11-05"}}
    result = plugin.on_pre_tool_call("mcp__ticktick__update_task", args)
    assert result["args"]["task"]["isAllDay"] is True


@pytest.mark.parametrize("tool", ["mcp__ticktick__batch_add_tasks", "mcp__ticktick__batch_update_tasks"])
def test_batches_fix_only_the_items_that_need_it(tool):
    timed = {"title": "b", "dueDate": "2026-11-05T19:15:00+0200"}
    broken = {"title": "c", "dueDate": "garbage"}
    args = {"tasks": [{"title": "a", "dueDate": "2026-11-05"}, timed, broken]}
    tasks = plugin.on_pre_tool_call(tool, args)["args"]["tasks"]
    assert tasks == [{"title": "a", **ALL_DAY_NOV_5}, timed, broken]


def test_batch_with_nothing_to_fix_is_left_alone():
    args = {"tasks": [{"dueDate": "2026-11-05T19:15:00+0200"}]}
    assert plugin.on_pre_tool_call("mcp__ticktick__batch_add_tasks", args) is None


def test_the_single_underscore_tool_name_is_covered():
    assert plugin.on_pre_tool_call("mcp_ticktick_create_task", {"task": {"dueDate": "2026-11-05"}}) is not None


@pytest.mark.parametrize(
    "tool", ["mcp__gmail__create_task", "mcp__ticktick__get_task_by_id", "mcp__ticktick__filter_tasks", "terminal", "tool_call"]
)
def test_other_tools_are_ignored(tool):
    assert plugin.on_pre_tool_call(tool, {"task": {"dueDate": "2026-11-05"}}) is None


def test_args_that_are_not_a_dict_pass_through():
    assert plugin.on_pre_tool_call(CREATE, None) is None


def test_the_original_args_are_not_mutated():
    args = {"task": {"title": "x", "dueDate": "2026-11-05"}}
    before = copy.deepcopy(args)
    plugin.on_pre_tool_call(CREATE, args)
    assert args == before


def test_a_rewrite_logs_the_title(caplog):
    with caplog.at_level("INFO"):
        hook({"title": "Купить хлеб", "dueDate": "2026-11-05"})
    assert "Купить хлеб" in caplog.text


def test_register_hooks_pre_tool_call():
    seen = {}

    class Ctx:
        def register_hook(self, name, callback):
            seen[name] = callback

    plugin.register(Ctx())
    assert seen == {"pre_tool_call": plugin.on_pre_tool_call}
