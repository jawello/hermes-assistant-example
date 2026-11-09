# Backups and restore

What it takes to bring the estate up on a new VPS: git (code, compose,
configs), `secrets.sops.yaml` (decrypted with the age key, which lives off
the server), and the nightly volume archives from `ops/backup.sh`.

## What is archived

`ops/backup.sh` runs from root's crontab, nightly
and writes `/var/backups/personal-assistant/<name>-<date>.tar.gz`, kept
7 days. The `VOLUMES` table in the script is the list; each entry says why.

Not archived, because they come back by themselves:

| Volume | Comes back from |
|---|---|
| `personal-assistant_vaultwarden_tls` | `vault-cert-init` generates a new self-signed pair |
| `personal-assistant_gmail_state` | empty |
| `personal-assistant_shared_files` | scratch, pruned by `shared-janitor` |
| `personal-assistant_hindsight_data` | model caches; the memory graph is in `hindsight_db_data` |

A run that cannot archive a volume still archives the rest, then exits 1
and names the volume in `/var/log/personal-assistant-backup.log`.

## Off-site: Google Drive

After the archives, `offsite_sync` mirrors the backup directory to the user's
Google Drive with rclone (`rclone/rclone` in a container):

- Through a crypt remote: Drive holds only encrypted files with encrypted
  names, in the folder `personal-assistant-backups`.
- OAuth client is gmail-mcp's (`GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`);
  the token is its own, scope `drive.file`, so rclone sees only the files
  it wrote.
- Same 7 days as on the VPS; deletions skip the Drive trash.

Secrets, all in the `env:` map of `secrets.sops.yaml`, written by
`ops/gdrive-backup-auth.sh` (run on the laptop; it prints no secret):

| Key | What |
|---|---|
| `BACKUP_GDRIVE_TOKEN_B64` | rclone token JSON, base64 |
| `BACKUP_CRYPT_PASSWORD`, `BACKUP_CRYPT_SALT` | crypt keys, rclone-obscured |

The crypt keys are created once and never replaced: new ones cannot read
what is already on Drive. Without them the Drive copy is unreadable, so they
are recoverable only through git + the age key. When the token dies (a run
logs `off-site: rclone sync exited`), run the script again: it replaces the
token and keeps the keys.

Without the keys every run logs
`off-site: Google Drive not configured, skipping`.

### Fetch archives from Drive

From a checkout of this repo on the new host, after the first deploy has
written `.env` (it holds the keys above):

```bash
BACKUP_DIR=/restore bash -c 'source ops/backup.sh && rclone_offsite rw copy offsite: /data'
```

## Restore one volume

On the new host, after the first deploy has created the volumes, stop the
service that owns the volume, then:

```bash
docker compose stop <service>
docker run --rm -v <volume>:/dst -v /path/to/archives:/b:ro debian:bookworm-slim \
  sh -c 'find /dst -mindepth 1 -delete && tar -xzf /b/<name>-<date>.tar.gz -C /dst'
docker compose start <service>
```

The volume names are the ones in the `VOLUMES` table.

## Restore check

A restore into a scratch volume shows that an archive is usable without
touching the live one:

```bash
docker volume create restore-check
docker run --rm -v restore-check:/dst -v /var/backups/personal-assistant:/b:ro \
  debian:bookworm-slim tar -xzf /b/<name>-<date>.tar.gz -C /dst
docker run --rm -v restore-check:/a:ro -v <volume>:/b:ro debian:bookworm-slim \
  diff -r /a /b
docker volume rm restore-check
```
