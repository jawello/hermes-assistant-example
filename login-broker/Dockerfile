FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

# nodejs/npm: the Bitwarden CLI. ca-certificates: TLS to Vaultwarden.
# The Playwright python package ships its own node driver; it connects to a
# remote Chromium over CDP, so no browser binary is downloaded here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Pinned deliberately, and only in step with Vaultwarden (docker-compose.yml):
# CLI 2026.7.0 died on `bw list items` against Vaultwarden 1.36.0, and
# 2026.9.1 cannot even log in to it ("KDF config response does not contain a
# valid KDF type") — clients 2026.8+ need Vaultwarden 1.37.2+. An unpinned
# install once took browser_login down (2026-08-10). Re-test login and list
# against the running Vaultwarden before bumping either side.
RUN npm install -g @bitwarden/cli@2026.9.1

COPY . /opt/login-broker
RUN pip install --no-cache-dir /opt/login-broker

EXPOSE 8000

CMD ["login-broker"]
