# SQLite 3.46.1 in Debian trixie (and so in python:3.12-slim) carries the
# WAL-reset corruption bug (https://sqlite.org/wal.html#walresetbug); hermes
# then refuses WAL for new databases and only warns about state.db, which
# already is WAL. Same fixed build as upstream's own image: version, checksum
# and flags copied from hermes-agent v0.21.5's Dockerfile.
FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d AS sqlite_build
ARG SQLITE_AUTOCONF_VERSION=3530400
ARG SQLITE_SHA256=0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL --retry 3 -o /tmp/sqlite.tar.gz \
        "https://sqlite.org/2026/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz" \
    && echo "${SQLITE_SHA256}  /tmp/sqlite.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/sqlite.tar.gz -C /tmp \
    && cd "/tmp/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}" \
    && CFLAGS="-O2 -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS \
        -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_RTREE \
        -DSQLITE_ENABLE_GEOPOLY -DSQLITE_ENABLE_COLUMN_METADATA \
        -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_ENABLE_DBSTAT_VTAB \
        -DSQLITE_ENABLE_DBPAGE_VTAB -DSQLITE_ENABLE_MATH_FUNCTIONS \
        -DSQLITE_ENABLE_PREUPDATE_HOOK -DSQLITE_ENABLE_SESSION \
        -DSQLITE_SECURE_DELETE -DSQLITE_THREADSAFE=1 \
        -DSQLITE_MAX_VARIABLE_NUMBER=250000" \
        ./configure --prefix=/opt/sqlite-fixed --disable-static \
    && make -j"$(nproc)" \
    && make install

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

# hermes-agent stopped publishing to PyPI at 0.19.0, and its setup.py now
# refuses to build a wheel: it resolves locales, skills and plugin manifests
# from a source checkout at runtime. So the image carries the tag's source
# tree (an immutable commit tarball) as an editable install, the form upstream
# supports outside its own image and Nix. HERMES_COMMIT is tag v2026.9.24 =
# hermes-agent 0.21.5.
ARG HERMES_COMMIT=f97608f178d1ffeca59860195ab7da295f7c8e5f
ARG HERMES_SRC=/opt/hermes-agent
# The hindsight memory provider left hermes core in 0.21 for Vectorize's
# repo; this is the commit hermes' own plugin catalog pins (hindsight 1.0.1).
ARG HINDSIGHT_PLUGIN_COMMIT=176f8c2de1369f569c489b831d143b78128b5535

# ffmpeg: voice memos; ripgrep+git: agent tools; nodejs/npm: MCP ecosystem;
# procps: pgrep for the compose healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git ffmpeg ripgrep nodejs npm procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=sqlite_build /opt/sqlite-fixed/lib/libsqlite3.so.3.53.4 /usr/local/lib/
RUN ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so.0 \
    && ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so \
    && printf '/usr/local/lib\n' > /etc/ld.so.conf.d/000-sqlite-fixed.conf \
    && ldconfig \
    && python -c "import sqlite3, sys; \
sys.exit(f'linked SQLite {sqlite3.sqlite_version} still has the WAL-reset bug') \
    if sqlite3.sqlite_version_info < (3, 51, 3) else None"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Everything hermes would otherwise lazy-install on first use goes in at build
# time, at the pins hermes declares: telegram (platform), edge-tts (voice
# replies), fal (images), google (the bundled google-workspace skill, used for
# Drive) and the mcp 2.x client. A runtime install means a slow first message
# after every deploy and a pip run inside the container that a restart undoes.
RUN mkdir -p "${HERMES_SRC}" \
    && curl -fsSL "https://github.com/NousResearch/hermes-agent/archive/${HERMES_COMMIT}.tar.gz" \
        | tar -xz -C "${HERMES_SRC}" --strip-components=1 \
    && pip install --no-cache-dir -e "${HERMES_SRC}[mcp,edge-tts,fal,google]" \
        "python-telegram-bot[webhooks]==22.8"

# Copied into hermes' bundled memory-provider directory rather than installed
# with `hermes plugins install`: that writes under HERMES_HOME, which is the
# data volume, and would fetch from GitHub on a running container. Bundled
# providers win discovery, and the client pins are the plugin's own.
RUN curl -fsSL "https://github.com/vectorize-io/hindsight/archive/${HINDSIGHT_PLUGIN_COMMIT}.tar.gz" \
        | tar -xz -C /tmp \
    && mv "/tmp/hindsight-${HINDSIGHT_PLUGIN_COMMIT}/hindsight-integrations/hermes" \
        "${HERMES_SRC}/plugins/memory/hindsight" \
    && rm -rf "/tmp/hindsight-${HINDSIGHT_PLUGIN_COMMIT}" \
        "${HERMES_SRC}/plugins/memory/hindsight/tests" "${HERMES_SRC}/plugins/memory/hindsight/docs" \
    && pip install --no-cache-dir "hindsight-client==0.10.2" "hindsight-embed==0.10.2"

# Carries the gateway session onto the MCP loop, so an MCP server's
# elicitation/create reaches Telegram instead of a stdin this container does
# not have (every confirmation was answered "deny", 2026-09-21). Still needed
# on 0.21.5. The patch fails the build if upstream's scheduling helper changes
# shape, so an unpatched image cannot ship quietly.
COPY hermes/patches/mcp_session_context.py /tmp/mcp_session_context.py
RUN python /tmp/mcp_session_context.py "${HERMES_SRC}/tools/mcp_tool_loop.py" \
    && rm /tmp/mcp_session_context.py

# The silence check matches "[SILENT]" but not "[ SILENT ]", and the
# suggestions job once answered with the spaced form, which reached Telegram
# as text (2026-09-26). The patch drops spaces inside the brackets and fails
# the build if the check changes shape.
COPY hermes/patches/cron_silence_tokens.py /tmp/cron_silence_tokens.py
RUN python /tmp/cron_silence_tokens.py "${HERMES_SRC}" && rm /tmp/cron_silence_tokens.py

# Tool search hides every MCP tool behind a three-call bridge even when a cron
# job, pinned to one server, has 13 tools (~3K tokens); the Telegram pass spent
# a third of its calls finding its tools (2026-10-05). The floor keeps light
# surfaces direct; the audit fields record what each fire saw and cost, so the
# floor can be judged later. Both fail the build on a shape change.
COPY hermes/patches/tool_search_min_tokens.py hermes/patches/cron_usage_audit_fields.py /tmp/
RUN python /tmp/tool_search_min_tokens.py "${HERMES_SRC}" \
    && python /tmp/cron_usage_audit_fields.py "${HERMES_SRC}" \
    && rm /tmp/tool_search_min_tokens.py /tmp/cron_usage_audit_fields.py

# hindsight's tool wrapper JSON-escapes non-ASCII, so a Russian recall reached
# the model as \uXXXX: ~13K characters of facts cost ~29K tokens of input
# (2026-10-09). The patch keeps UTF-8 and fails the build on a shape change.
COPY hermes/patches/hindsight_tool_utf8.py /tmp/hindsight_tool_utf8.py
RUN python /tmp/hindsight_tool_utf8.py "${HERMES_SRC}" && rm /tmp/hindsight_tool_utf8.py

# Cron passes were retained with the job's prompt, and extraction retold it as
# facts: 648 of 1010 cron units on 2026-11-02. Cron now retains only a real reply.
COPY hermes/patches/hindsight_cron_reply_only.py /tmp/hindsight_cron_reply_only.py
RUN python /tmp/hindsight_cron_reply_only.py "${HERMES_SRC}" && rm /tmp/hindsight_cron_reply_only.py

# Each cron pass queued a recall for a next turn it does not have: 15 of 15 were
# dropped after 9-14 s on 2026-11-03/04, and one shared a minute with the memory peak.
COPY hermes/patches/hindsight_cron_no_prefetch.py /tmp/hindsight_cron_no_prefetch.py
RUN python /tmp/hindsight_cron_no_prefetch.py "${HERMES_SRC}" && rm /tmp/hindsight_cron_no_prefetch.py

# Background recall searched by the whole message, with the gateway's notes first: the
# user's words were cut off in 7 of 48 replies, and rerank time grows with query length.
COPY hermes/patches/hindsight_query_own_text.py /tmp/hindsight_query_own_text.py
RUN python /tmp/hindsight_query_own_text.py "${HERMES_SRC}" && rm /tmp/hindsight_query_own_text.py

# With recall_sync on, the search runs inside hermes' 8 s prefetch wait, and a cron
# pass missed it by 0.2 s on 2026-11-05. The wait ends when the result arrives.
COPY hermes/patches/hindsight_prefetch_timeout.py /tmp/hindsight_prefetch_timeout.py
RUN python /tmp/hindsight_prefetch_timeout.py "${HERMES_SRC}" && rm /tmp/hindsight_prefetch_timeout.py

# /skills diff showed a staged batch as "(batch on '')", and the bot stages rules
# as batches: the first rule under the approval gate could not be reviewed
# (2026-11-06). Batches now render as diffs; the build fails on a shape change.
COPY hermes/patches/skill_diff_batch.py /tmp/skill_diff_batch.py
RUN python /tmp/skill_diff_batch.py "${HERMES_SRC}" && rm /tmp/skill_diff_batch.py

# A voice turn's 🔊 line is spoken on the Station (via alice-bridge /speak)
# before the rest goes to Telegram (2026-11-08). The runtime module lives next
# to the webhook platform; the patch calls it from send() and fails the build
# on a shape change.
COPY hermes/patches/alice_speech.py ${HERMES_SRC}/gateway/platforms/alice_speech.py
COPY hermes/patches/webhook_speak.py /tmp/webhook_speak.py
RUN python /tmp/webhook_speak.py "${HERMES_SRC}" && rm /tmp/webhook_speak.py

# Bundled, like the hindsight provider, so it lives in the image and not on the
# data volume. It rewrites a TickTick date with no time into an all-day task.
COPY hermes/plugins/ticktick_untimed ${HERMES_SRC}/plugins/ticktick_untimed

COPY zenmoney-cli /opt/zenmoney-cli
RUN pip install --no-cache-dir /opt/zenmoney-cli

COPY hermes/config.yaml hermes/SOUL.md hermes/hindsight-config.json /opt/defaults/
COPY hermes/skills /opt/defaults/skills
COPY hermes/entrypoint.sh /usr/local/bin/entrypoint.sh
# The agent's way to store a fact when it runs on a schedule: hermes disables
# the memory tool for cron jobs, so the gmail digest would otherwise read the
# mail and remember none of it.
COPY hermes/bin/remember /usr/local/bin/remember
# The ledger of morning task suggestions: the cron run that proposes them and
# the chat session where the user answers share no context, only this file.
COPY hermes/bin/suggestions /usr/local/bin/suggestions
# Where each reading pass (hourly mail, morning Telegram) starts: the last
# finished pass of the same source. `mail-window` is its gmail name, kept for
# the mail jobs and skill. Linked here rather than copied: COPY would
# dereference the repo's symlink into a second copy of the script.
COPY hermes/bin/pass-window /usr/local/bin/pass-window
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/remember /usr/local/bin/suggestions \
        /usr/local/bin/pass-window \
    && ln -s pass-window /usr/local/bin/mail-window

ENV HERMES_HOME=/data/hermes \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
