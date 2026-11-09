# How it was built

Almost all of this repo was written by an AI coding agent working with the
owner, and much of the assistant's behaviour was then refined by the
assistant itself. Two loops, each with a human gate.

## Loop one: design first, then code

Every change of any size went the same way:

1. **Talk it through.** The coding agent asks what problem this solves and
   what would make it a failure even if it technically works, before
   proposing anything.
2. **Write a design.** A short document with the goal, the non-goals, the
   anti-goals and the decisions the owner made. A fresh reviewer agent reads
   it as the builder would and asks what is still unclear; only the owner's
   answers go back in.
3. **Write a plan.** Small tasks, each with the exact files, interfaces and
   tests, so the work can be checked step by step.
4. **Build with tests first.** Each task starts with a failing test. Live
   behaviour is measured, not assumed — the TickTick skill's "R" rules, for
   instance, each come from a real call against the server.
5. **Review the whole change** with a fresh agent, then fix what it found.

Comments in the code keep the reason and the date of the incident behind a
decision ("the suggestions job once answered with the spaced form, which
reached Telegram as text"), because the next change is usually made by an
agent that was not there.

## Loop two: the assistant edits its own skills

Skills are Markdown instructions under `hermes/skills/`. When the owner
corrects the assistant ("from now on…"), the assistant proposes a skill change
with `skill_manage`. With `write_approval` on, nothing is saved until the
owner reviews the diff and approves it in Telegram. Approved changes are then
brought back into the repo, so the repo stays the source of truth and the next
image ships the rule.

Rules that fit no skill go to a task list for the coding agent; facts about
people and places go to memory, never into a skill. That split is what made
it possible to publish this snapshot: the skills hold method, memory holds
life.

## Publishing this snapshot

The original repo stays private. This one is produced from it by a small
default-deny tool: an exact list of files, deterministic edits for files that
change often, hand-written public versions of the skills, and a scan for
personal values, secrets and their shapes before anything is pushed. A person
reads every published change, because a fact can be implied without any
keyword in it.
