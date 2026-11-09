# the user's own Telegram account as a read-only MCP server for the assistant.
# The server is chigwell/telegram-mcp, pinned to one commit (tag v3.2.65); it
# publishes no image, so it is built here from the commit tarball with the
# dependency versions and hashes of upstream's own uv.lock.
FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a AS build

ARG TELEGRAM_MCP_COMMIT=6072a24ff02b166ef35e322a6d15b2a795181f23

SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

WORKDIR /src
RUN wget -qO- "https://github.com/chigwell/telegram-mcp/archive/${TELEGRAM_MCP_COMMIT}.tar.gz" \
        | tar -xz --strip-components=1

# uv only reads the lock; the install itself is pip with --require-hashes, so
# a dependency that differs from upstream's lock fails the build.
RUN pip install --no-cache-dir "uv==0.7.21" \
    && uv export --frozen --no-dev --no-emit-project --no-header -o /tmp/requirements.txt \
    && pip install --no-cache-dir --prefix=/install --require-hashes -r /tmp/requirements.txt

# list_chats gains last_message_at, so the morning pass can skip quiet chats.
# Fails the build if upstream's list_chats changes shape.
COPY patches/list_chats_last_message.py /tmp/list_chats_last_message.py
RUN python /tmp/list_chats_last_message.py /src/telegram_mcp/tools/chats.py

# list_chats gains since, so a pass gets only the chats that moved since the
# last one. Applied after the patch above; fails the build on a shape change.
COPY patches/list_chats_since.py /tmp/list_chats_since.py
RUN python /tmp/list_chats_since.py /src/telegram_mcp/tools/chats.py

# list_messages gains since, so a pass reads only what is new since the last
# one, newest first. Fails the build if upstream's list_messages changes shape.
COPY patches/list_messages_since.py /tmp/list_messages_since.py
RUN python /tmp/list_messages_since.py /src/telegram_mcp/tools/messages.py

# Our own modules, registered by one import block. Fails the build if
# upstream's tools/__init__.py changes shape.
# The confirmed send: an allowlist + a confirmation on every message.
COPY send_gate/__init__.py send_gate/core.py send_gate/tools.py /src/telegram_mcp/tools/send_gate/
# The history search: several words, dates, oldest first, short rows.
COPY history_search/__init__.py history_search/core.py history_search/tools.py /src/telegram_mcp/tools/history_search/
COPY patches/register_modules.py /tmp/register_modules.py
RUN python /tmp/register_modules.py /src/telegram_mcp/tools/__init__.py

FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a

COPY --from=build /install /usr/local
WORKDIR /app
COPY --from=build /src/main.py /src/sanitize.py ./
COPY --from=build /src/telegram_mcp ./telegram_mcp
# Parks the container instead of crash-looping when the session is refused;
# see run.sh.
COPY --chmod=755 run.sh ./run.sh

# /data holds the voice transcript cache and the server's small state files
# (aliases, feed); it is a named volume, so it is created owned by app here.
RUN adduser -D -u 10001 app \
    && mkdir -p /data/transcripts /data/state \
    && chown -R app:app /data

USER app

ENV PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    TELEGRAM_TRANSCRIPT_CACHE_DIR=/data/transcripts \
    XDG_STATE_HOME=/data/state

EXPOSE 8000
CMD ["/app/run.sh"]
