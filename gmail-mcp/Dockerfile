FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

# ca-certificates: TLS to Google and to Portal. No node, no browser — the
# interactive OAuth flow runs on a laptop, never here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/gmail-mcp
RUN pip install --no-cache-dir /opt/gmail-mcp

EXPOSE 8000

CMD ["gmail-mcp"]
