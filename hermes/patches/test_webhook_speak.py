"""Tests for the patch that hooks Station speech into hermes' webhook delivery.

The patch must put the speech call right before the cross-platform delivery in
``send()``, leave ``_direct_deliver`` (deliver_only routes) alone, and refuse a
file that no longer looks like 0.21.5's.
"""
import pytest

import webhook_speak as patch

# Shaped like gateway/platforms/webhook.py, hermes-agent 0.21.5 (send and _direct_deliver).
WEBHOOK = '''\
class WebhookAdapter:
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if is_autonomous_silence_response(content):
            logger.info("[webhook] Response for %s is a silence marker — not delivering", chat_id)
            return SendResult(success=True)
        delivery = self._delivery_info.get(chat_id, {})
        deliver_type = delivery.get("deliver", "log")
        if deliver_type == "log":
            logger.info("[webhook] Response for %s: %s", chat_id, content[:200])
            return SendResult(success=True)
        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)
        if self.gateway_runner and _is_known_platform(deliver_type):
            return await self._deliver_cross_platform(deliver_type, content, delivery)
        logger.warning("[webhook] Unknown deliver type: %s", deliver_type)
        return SendResult(success=False, error=f"Unknown deliver type: {deliver_type}")

    async def _direct_deliver(self, content: str, delivery: dict) -> SendResult:
        deliver_type = delivery.get("deliver", "log")
        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)
        return await self._deliver_cross_platform(deliver_type, content, delivery)
'''


def test_patch_inserts_speech_before_cross_platform_delivery():
    patched = patch.patched_webhook(WEBHOOK)
    send = patched.split("async def _direct_deliver")[0]
    hook = send.index("spoken = await speak_then_strip(content, delivery, metadata=metadata)")
    assert send.index("from gateway.platforms.alice_speech import speak_then_strip") < hook
    assert hook < send.index("return await self._deliver_cross_platform(deliver_type, content, delivery)")
    compile(patched, "webhook.py", "exec")


def _adapter(patched, sent):
    """Execute the patched send() with stubs around it."""
    import logging
    from typing import Any, Dict, Optional

    class SendResult:
        def __init__(self, success, error=None):
            self.success, self.error = success, error

    ns = {"Optional": Optional, "Dict": Dict, "Any": Any, "SendResult": SendResult,
          "logger": logging.getLogger("webhook-test"), "_is_known_platform": lambda t: t == "telegram",
          "is_autonomous_silence_response": lambda c: False}
    exec(compile(patched, "webhook.py", "exec"), ns)
    adapter = ns["WebhookAdapter"]()
    adapter.gateway_runner = object()
    adapter._delivery_info = {"c": {"deliver": "telegram", "deliver_extra": {"speak_on": "dev-1"}}}

    async def deliver(deliver_type, content, delivery):
        sent.append(content)
        return SendResult(True)
    adapter._deliver_cross_platform = deliver
    return adapter


def test_hook_failure_still_delivers(monkeypatch):
    import asyncio
    import sys
    monkeypatch.delitem(sys.modules, "gateway.platforms.alice_speech", raising=False)
    monkeypatch.setitem(sys.modules, "gateway", None)  # import fails
    sent = []
    adapter = _adapter(patch.patched_webhook(WEBHOOK), sent)
    asyncio.run(adapter.send("c", "answer\n🔊 Готово."))
    assert sent == ["answer\n🔊 Готово."]


def test_hook_passes_metadata(monkeypatch):
    import asyncio
    import sys
    import types
    seen = {}

    async def speak_then_strip(content, delivery, *, metadata=None):
        seen["metadata"] = metadata
        return "stripped"
    module = types.ModuleType("gateway.platforms.alice_speech")
    module.speak_then_strip = speak_then_strip
    for name in ("gateway", "gateway.platforms"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "gateway.platforms.alice_speech", module)
    sent = []
    adapter = _adapter(patch.patched_webhook(WEBHOOK), sent)
    asyncio.run(adapter.send("c", "answer", metadata={"_interim_send": True}))
    assert sent == ["stripped"] and seen["metadata"] == {"_interim_send": True}


def test_direct_deliver_is_untouched():
    patched = patch.patched_webhook(WEBHOOK)
    assert patched.split("async def _direct_deliver")[1] == WEBHOOK.split("async def _direct_deliver")[1]
    assert patched.count("speak_then_strip(content, delivery, metadata=metadata)") == 1


def test_patch_is_idempotent():
    once = patch.patched_webhook(WEBHOOK)
    assert patch.patched_webhook(once) == once


def test_patch_refuses_changed_upstream():
    changed = WEBHOOK.replace("if self.gateway_runner and _is_known_platform(deliver_type):",
                              "if _is_known_platform(deliver_type):")
    with pytest.raises(patch.PatchError):
        patch.patched_webhook(changed)


def test_main_patches_file_in_place(tmp_path):
    target = tmp_path / "gateway" / "platforms" / "webhook.py"
    target.parent.mkdir(parents=True)
    target.write_text(WEBHOOK)
    assert patch.main(["webhook_speak.py", str(tmp_path)]) == 0
    assert "speak_then_strip" in target.read_text()


def test_hook_can_drop_a_message(monkeypatch):
    import asyncio
    import sys
    import types

    async def speak_then_strip(content, delivery, *, metadata=None):
        return None
    module = types.ModuleType("gateway.platforms.alice_speech")
    module.speak_then_strip = speak_then_strip
    for name in ("gateway", "gateway.platforms"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "gateway.platforms.alice_speech", module)
    sent = []
    adapter = _adapter(patch.patched_webhook(WEBHOOK), sent)
    result = asyncio.run(adapter.send("c", "👁️ Hindsight — recalled 2 memories"))
    assert sent == [] and result.success
