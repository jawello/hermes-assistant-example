# Vaultwarden (AI-browsing credential vault)

Dedicated Vaultwarden instance for the AI agent's site credentials.
the user's personal passwords do NOT live here. The service publishes no port
at all: it sits only on the internal `vault` network at the static IP
`172.29.0.10` (subnet `172.29.0.0/24`), and the login-broker talks to it
over that network (`https://vaultwarden`). Docker never publishes `ports:`
for a container whose only network is internal, so access is an SSH tunnel
straight to the container IP.

The vault serves HTTPS with a self-signed cert (generated once by the
`vault-cert-init` service into the `vaultwarden_tls` volume, SANs
`localhost`/`vaultwarden`/`127.0.0.1`). The modern Bitwarden web vault
refuses to run over http even on localhost, so HTTPS is required. Browsers
show a one-time self-signed warning — accept it; it is still a secure
context. The login-broker trusts this cert via `NODE_EXTRA_CA_CERTS`.

## Access (the user)

    ssh -L 8222:172.29.0.10:80 deploy@203.0.113.10

Then open https://localhost:8222 (web vault) or https://localhost:8222/admin
(admin panel; token = sops key `VAULTWARDEN_ADMIN_TOKEN`, extract without
printing: `sops -d --extract '["env"]["VAULTWARDEN_ADMIN_TOKEN"]'
secrets.sops.yaml | xclip -selection clipboard`). Accept the browser's
self-signed-cert warning once.

## Bootstrap (one-time)

1. In `/admin` → Users → Invite: `ai-browser@vault.local`.
2. Open https://localhost:8222 → Create account with that email (invited
   emails may register even though signups are disabled). Generate the
   master password locally; store it in sops as `BW_MASTER_PASSWORD`
   (needed later by the login-broker).
3. Add login items: username, password, URI with match type Host
   (exact host — the login-broker refuses anything else), TOTP seed
   where the site has 2FA.

## Restore

The `vaultwarden_data` volume is in the nightly backup
(`/var/backups/personal-assistant/vaultwarden-data-*.tar.gz`). To restore:

    docker compose stop vaultwarden
    docker run --rm -v personal-assistant_vaultwarden_data:/dst \
      -v /var/backups/personal-assistant:/backups:ro alpine \
      sh -c 'rm -rf /dst/* && tar -xzf /backups/vaultwarden-data-<DATE>.tar.gz -C /dst'
    docker compose start vaultwarden

Vault data is encrypted at rest with keys derived from account master
passwords, so a leaked archive alone does not expose credentials.

## Rotating the TLS cert

The self-signed cert lives on the `vaultwarden_tls` volume and is not backed
up (it is regenerable). To rotate: remove the volume and restart — the
`vault-cert-init` service regenerates it.

    docker compose stop vaultwarden
    docker volume rm personal-assistant_vaultwarden_tls
    docker compose up -d

After rotation, re-accept the browser warning, and re-pin the new cert for
the login-broker (`NODE_EXTRA_CA_CERTS`) once that exists.
