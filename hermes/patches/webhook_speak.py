#!/usr/bin/env python3
"""Speak a voice turn's short answer on the Station before it goes to Telegram.

**Why.** the user wants the Station to say the answer to a voice command when
the agent's turn ends (design: design notes not published-
replies-design.md). hermes' webhook platform has no delivery target or hook
for that: `send()` hands the agent's answer straight to the cross-platform
delivery. A plugin hook cannot change what is delivered.

**The patch.** In `gateway/platforms/webhook.py` `send()`, right before the
cross-platform delivery, pass the content through
`gateway.platforms.alice_speech.speak_then_strip` (installed next to it from
`hermes/patches/alice_speech.py`). That function does nothing for a route
without `deliver_extra.speak_on`, so every other route is unchanged.
`_direct_deliver` (deliver_only routes: no agent, no 🔊 line) is left alone.

The patch fails the build if the branch no longer looks like 0.21.5's, so an
unpatched image cannot ship quietly.

Usage: `python webhook_speak.py <hermes source dir>`
"""
import sys
from pathlib import Path


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM = (
    "        if self.gateway_runner and _is_known_platform(deliver_type):\n"
    "            return await self._deliver_cross_platform(deliver_type, content, delivery)\n"
)
PATCHED = (
    "        if self.gateway_runner and _is_known_platform(deliver_type):\n"
    "            # Station speech for voice turns (personal-assistant webhook_speak.py).\n"
    "            # Never at the cost of the Telegram answer: any failure delivers as-is.\n"
    "            try:\n"
    "                from gateway.platforms.alice_speech import speak_then_strip\n"
    "                spoken = await speak_then_strip(content, delivery, metadata=metadata)\n"
    "                if spoken is None:  # dropped on purpose (a voice turn's recall line)\n"
    "                    return SendResult(success=True)\n"
    "                content = spoken\n"
    "            except Exception:\n"
    "                logger.warning(\"[webhook] alice speech hook failed\", exc_info=True)\n"
    "            return await self._deliver_cross_platform(deliver_type, content, delivery)\n"
)


def patched_webhook(source: str) -> str:
    """The webhook platform whose agent-mode delivery speaks first."""
    if PATCHED in source:
        return source
    found = source.count(UPSTREAM)
    if found != 1:
        raise PatchError(
            f"expected one cross-platform delivery branch in send(), found {found} "
            "— upstream changed gateway/platforms/webhook.py; re-check this patch"
        )
    return source.replace(UPSTREAM, PATCHED, 1)


def main(argv: list[str]) -> int:
    webhook = Path(argv[1]) / "gateway" / "platforms" / "webhook.py"
    source = webhook.read_text()
    patched = patched_webhook(source)
    if patched == source:
        print(f"{webhook}: already patched")
    else:
        webhook.write_text(patched)
        print(f"{webhook}: speech hooked into webhook delivery")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
