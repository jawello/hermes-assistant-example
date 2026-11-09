"""Tests for the patch that makes background recall search by the user's own words.

The gateway puts notes before the user's text. They used to lead the recall query and
could push the user's words past the 800-character cut.
"""
import threading

import pytest

import hindsight_query_own_text as patch

# Shaped like plugins/memory/hindsight/__init__.py, hindsight plugin 1.0.1 as pinned by
# hermes-agent 0.21.5.
PLUGIN = '''\
import logging
import threading

logger = logging.getLogger(__name__)


class MemoryProvider:
    pass


class HindsightMemoryProvider(MemoryProvider):
    def __init__(self, recall_max_input_chars=800):
        self._recall_max_input_chars = recall_max_input_chars
        self._prefetch_thread = None
        self.searched = []

    def _recall(self, query):
        self.searched.append(query)
        return []

    def _do_recall(self, query: str) -> tuple[str, int]:
        """One recall/reflect for *query* (background prefetch and ``recall_sync`` paths)
        -> (text, memory count); the count is 0 for reflect (synthesis) and on error."""
        if self._recall_max_input_chars:
            query = query[: self._recall_max_input_chars]
        self._recall(query)
        return "", 0

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        def _run():
            self._do_recall(query)

        self._prefetch_thread = threading.Thread(target=_run)
        self._prefetch_thread.start()

    def _tool_recall(self, query):
        self._recall(query)
'''

# The lines of gateway/run_inbound.py and gateway/run.py that word the notes.
RUN_INBOUND = r'''
            _who = " your previous message" if getattr(event, "reply_to_is_own_message", False) else ""
            message_text = f'[Replying to{_who}: "{reply_text}"]\n\n{message_text}'
            ("an audio file attachment", "audio", "transcribe or process", "a transcription or media tool", audio_file_paths),
            ("a video attachment", "video", "inspect or process", "a video analysis or media tool", video_paths),
                    f"[The user sent {kind}: '{_display}'. "
                        f"[The user sent an image~ Here's what I can see:\n{description}]\n"
                        f"[If you need a closer look, use vision_analyze with "
                        f"image_url: {path} ~]"
'''
RUN = r'''
            f"[The user sent a text document: '{display_name}'. Its content has been included below. "
            f"[The user sent a text document: '{display_name}'. It is saved at: {agent_path}. "
        f"[The user sent a document: '{display_name}'. It is saved at: {agent_path}. "
'''

# The lines of cron/scheduler_prompt.py that open and close the hint.
SCHEDULER_PROMPT = r'''
    "[IMPORTANT: You are running as a scheduled cron job. "
    "context for this run, not as a request to schedule another job.]\n\n"
'''

# The hint itself, as cron/scheduler_prompt.py builds it.
CRON_HINT = (
    "[IMPORTANT: You are running as a scheduled cron job. "
    "DELIVERY: Your final response will be automatically delivered "
    "to the user — do NOT use send_message or try to deliver "
    "the output yourself. Just produce your report/output as your "
    "final response and the system handles the rest. "
    "SILENT: If there is genuinely nothing new to report, respond "
    'with exactly "[SILENT]" (nothing else) to suppress delivery. '
    "[SILENT] is a literal ASCII control token — never translate or "
    "rephrase it, whatever language the rest of your answer uses. "
    "Never combine [SILENT] with content — either report your "
    "findings normally, or say [SILENT] and nothing more. "
    "FAILURE: If a delegated child fails and this cron run must be "
    "recorded as failed, put [CRON_FAILURE] on the first line by itself, "
    "then explain the child failure on following lines. "
    "RECURSION: This is a run of an EXISTING scheduled job — execute "
    "the task now. NEVER create or update a cron job because of "
    "recurring or future-schedule language in the task prompt below; "
    'treat phrasing like "each Monday" or "every day at 9" as '
    "context for this run, not as a request to schedule another job.]\n\n"
)
JOB_PROMPT = "Hourly mail pass. Run mail-window start and use the term it prints as the query."

LONG_OWN = "когда следующая поездка и что нужно подготовить"  # 47 characters
QUOTE = (
    "Готово, всё записал и поставил напоминание на утро. Вот что получилось по списку: "
    "первый пункт закрыт, второй перенесён на следующую неделю, третий ждёт ответа из "
    "сервиса. Если что-то поменяется, напишу отдельно, а пока ничего делать не нужно. "
    "Остальное оставил без изменений, как договорились."
)
DESCRIPTION = "На фото чек из магазина с тремя позициями и итоговой суммой внизу."
IMAGE = (
    f"[The user sent an image~ Here's what I can see:\n{DESCRIPTION}]\n"
    "[If you need a closer look, use vision_analyze with image_url: /data/cache/img.jpg ~]"
)
DOCUMENT = (
    "[The user sent a document: 'lease.pdf'. It is saved at: /data/cache/doc_1_lease.pdf. "
    "Its text is not inlined here (it's a binary format such as PDF or DOCX). To read it, "
    "extract the document's text yourself.]"
)


def provider(source: str = "", **kwargs):
    namespace = {}
    exec(compile(source or patch.patched_plugin(PLUGIN), "__init__.py", "exec"), namespace)
    return namespace["HindsightMemoryProvider"](**kwargs)


def searched(message: str, **kwargs) -> list[str]:
    p = provider(**kwargs)
    p._do_recall(message)
    return p.searched


def test_unpatched_reply_searches_by_the_quote_first():
    # The bug this patch exists for: the user's words come last and can be cut off.
    p = provider(PLUGIN)
    p._do_recall(f'[Replying to: "{QUOTE * 3}"]\n\n{LONG_OWN}')
    assert p.searched[0].startswith('[Replying to: "Готово')
    assert LONG_OWN not in p.searched[0]


@pytest.mark.parametrize("who", ["", " your previous message"])
def test_reply_with_enough_words_searches_by_them_alone(who):
    assert searched(f'[Replying to{who}: "{QUOTE}"]\n\n{LONG_OWN}') == [LONG_OWN]


def test_forty_characters_are_enough_on_their_own():
    words = "а" * 40
    assert searched(f'[Replying to: "{QUOTE}"]\n\n{words}') == [words]


def test_thirty_nine_characters_get_the_topic_of_the_quote():
    words = "а" * 39
    # 150 in all: the words, a blank line, then 109 characters of the quote.
    assert searched(f'[Replying to: "{QUOTE}"]\n\n{words}') == [f"{words}\n\n{QUOTE[:109]}"]


def test_short_reply_puts_the_words_before_the_quote():
    assert searched('[Replying to: "про поездку"]\n\nда, сделай') == ["да, сделай\n\nпро поездку"]


def test_spaces_around_the_words_do_not_count_towards_enough():
    words = "а" * 39
    assert searched(f'[Replying to: "про поездку"]\n\n  {words}\n') == [f"{words}\n\nпро поездку"]


def test_reply_with_no_words_searches_by_the_start_of_the_quote():
    assert searched(f'[Replying to: "{QUOTE}"]\n\n') == [QUOTE[:150]]


def test_a_quote_with_brackets_inside_does_not_end_the_note_early():
    quote = 'значения ["a", "b"] в списке'
    assert searched(f'[Replying to: "{quote}"]\n\n{LONG_OWN}') == [LONG_OWN]


def test_document_with_a_caption_searches_by_the_caption():
    assert searched(f"{DOCUMENT}\n\n{LONG_OWN}") == [LONG_OWN]


def test_document_with_no_caption_searches_by_its_name():
    assert searched(DOCUMENT) == ["lease.pdf"]


def test_text_document_with_no_caption_searches_by_its_name():
    note = (
        "[The user sent a text document: 'notes.txt'. Its content has been included below. "
        "The file is also saved at: /data/cache/doc_1_notes.txt]"
    )
    assert searched(note) == ["notes.txt"]


def test_document_with_a_short_caption_adds_its_name():
    assert searched(f"{DOCUMENT}\n\nчто тут") == ["что тут\n\nlease.pdf"]


@pytest.mark.parametrize(
    "note",
    [
        "[The user sent an audio file attachment: 'call.ogg'. It is saved at: /data/call.ogg. "
        "Its content is not inlined here.]",
        "[The user sent a video attachment: 'call.ogg'. It is saved at: /data/call.ogg. "
        "Its content is not inlined here.]",
    ],
)
def test_attachment_with_no_caption_searches_by_its_name(note):
    assert searched(note) == ["call.ogg"]


def test_image_with_no_caption_searches_by_the_description():
    assert searched(IMAGE) == [DESCRIPTION]


def test_image_with_a_short_caption_adds_the_description():
    assert searched(f"{IMAGE}\n\nсколько это") == [f"сколько это\n\n{DESCRIPTION}"]


def test_image_with_enough_words_searches_by_them_alone():
    assert searched(f"{IMAGE}\n\n{LONG_OWN}") == [LONG_OWN]


def test_stacked_notes_are_all_removed():
    assert searched(f'[Replying to: "{QUOTE}"]\n\n{IMAGE}\n\n{LONG_OWN}') == [LONG_OWN]


def test_stacked_notes_give_their_topics_in_order():
    assert searched(f'[Replying to: "про чек"]\n\n{IMAGE}\n\nда') == [
        f"да\n\nпро чек\n\n{DESCRIPTION}"
    ]


def test_cron_pass_searches_by_the_job_not_by_the_hint():
    # The hint is the same 1124 characters for every job, so it found the same memories for all.
    assert searched(CRON_HINT + JOB_PROMPT) == [JOB_PROMPT]


def test_a_long_job_prompt_is_cut_by_the_configured_limit():
    assert searched(CRON_HINT + "а" * 900, recall_max_input_chars=500) == ["а" * 500]


def test_a_note_with_no_words_and_no_topic_searches_nothing():
    # The only text would be instructions and a file path.
    p = provider()
    note = (
        "[The user sent an image but I couldn't quite see it this time (>_<) You can try "
        "looking at it yourself with vision_analyze using image_url: /data/cache/img.jpg]"
    )
    assert p._do_recall(note) == ("", 0)
    assert p.searched == []


def test_a_note_with_no_topic_still_lets_the_words_through():
    note = "[The user sent a voice message: /data/cache/voice.ogg (duration: 0:03)]"
    assert searched(f"{note}\n\nда") == ["да"]


@pytest.mark.parametrize(
    "message",
    [
        "да",
        LONG_OWN,
        "[x] купить хлеб",
        "[важно]\n\nне забыть про поездку",
        '[Replying to без кавычек]\n\nмой текст',
    ],
)
def test_a_message_with_no_gateway_note_is_searched_as_it_is(message):
    assert searched(message) == [message]


def test_the_800_character_cut_still_applies_to_own_words():
    words = "а" * 900
    assert searched(f'[Replying to: "{QUOTE}"]\n\n{words}') == ["а" * 800]


def test_the_800_character_cut_still_applies_to_a_plain_message():
    assert searched("а" * 900) == ["а" * 800]


def test_background_recall_searches_by_the_own_words():
    p = provider()
    p.queue_prefetch(f'[Replying to: "{QUOTE}"]\n\n{LONG_OWN}')
    assert isinstance(p._prefetch_thread, threading.Thread)
    p._prefetch_thread.join(timeout=5)
    assert p.searched == [LONG_OWN]


def test_the_recall_tool_keeps_the_query_the_model_wrote():
    p = provider()
    query = f'[Replying to: "{QUOTE}"]\n\n{LONG_OWN}'
    p._tool_recall(query)
    assert p.searched == [query]


def test_patch_is_idempotent():
    once = patch.patched_plugin(PLUGIN)
    assert patch.patched_plugin(once) == once


@pytest.mark.parametrize(
    "old, new",
    [
        ("if self._recall_max_input_chars:", "if self._max_chars:"),
        ("class HindsightMemoryProvider(MemoryProvider):", "class Provider(MemoryProvider):"),
    ],
)
def test_patch_refuses_an_unknown_plugin(old, new):
    with pytest.raises(patch.PatchError):
        patch.patched_plugin(PLUGIN.replace(old, new))


def hermes_tree(
    tmp_path, run_inbound: str = RUN_INBOUND, run: str = RUN, scheduler_prompt: str = SCHEDULER_PROMPT
):
    plugin = tmp_path / "plugins" / "memory" / "hindsight" / "__init__.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(PLUGIN)
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "run_inbound.py").write_text(run_inbound)
    (tmp_path / "gateway" / "run.py").write_text(run)
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "scheduler_prompt.py").write_text(scheduler_prompt)
    return plugin


def test_main_patches_the_file_in_place(tmp_path):
    plugin = hermes_tree(tmp_path)
    assert patch.main(["hindsight_query_own_text.py", str(tmp_path)]) == 0
    p = provider(plugin.read_text())
    p._do_recall(f'[Replying to: "{QUOTE}"]\n\n{LONG_OWN}')
    assert p.searched == [LONG_OWN]


@pytest.mark.parametrize(
    "run_inbound, run",
    [
        (RUN_INBOUND.replace("[Replying to{_who}", "[In reply to{_who}"), RUN),
        (RUN_INBOUND.replace(" your previous message", " my message"), RUN),
        (RUN_INBOUND.replace("Here's what I can see", "I can see"), RUN),
        (RUN_INBOUND.replace("a video attachment", "a video"), RUN),
        (RUN_INBOUND, RUN.replace("[The user sent a document", "[The user attached a document")),
    ],
)
def test_main_refuses_when_the_gateway_rewords_a_note(tmp_path, run_inbound, run):
    # A reworded note would quietly slip through the patch and lead the query again.
    plugin = hermes_tree(tmp_path, run_inbound, run)
    with pytest.raises(patch.PatchError):
        patch.main(["hindsight_query_own_text.py", str(tmp_path)])
    assert plugin.read_text() == PLUGIN


@pytest.mark.parametrize(
    "old, new",
    [
        ("[IMPORTANT: You are running", "[NOTE: You are running"),
        ("another job.]\\n\\n", "another job.]\\n"),
    ],
)
def test_main_refuses_when_the_scheduler_rewords_the_hint(tmp_path, old, new):
    # A reworded hint would lead every cron query again and take it past the 8 s limit.
    plugin = hermes_tree(tmp_path, scheduler_prompt=SCHEDULER_PROMPT.replace(old, new))
    with pytest.raises(patch.PatchError):
        patch.main(["hindsight_query_own_text.py", str(tmp_path)])
    assert plugin.read_text() == PLUGIN
