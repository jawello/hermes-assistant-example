# AI Browsing (headless Chromium)

The personal Hermes bot browses the web through a headless Chromium
container (`chromedp/headless-shell`) it drives over the Chrome DevTools
Protocol. Chromium has no host port: it sits on the internal `aibrowse`
network at a fixed IP and Hermes connects to it by IP.

## Why a static IP

Chrome's DevTools HTTP endpoints reject any request whose `Host` header is
not `localhost` or an IP address. Reaching Chromium by its compose service
name (`http://chromium:9222`) fails with "Host header is specified and is
not an IP address or localhost". So Chromium gets the static IP
`172.28.0.10` on the `aibrowse` bridge (subnet `172.28.0.0/24`), and Hermes
uses `browser.cdp_url: http://172.28.0.10:9222`. Hermes fetches
`/json/version` there and connects to the returned browser websocket.

## No credentials yet

Hermes' built-in `browser_type` types text through the model's context, so
it must never be used for passwords or 2FA codes. Logging in is handled by
a separate login-broker (later work) that injects credentials outside the
model. Until then the bot browses only public / already-authenticated pages.

## Cookies

Chromium persists its profile (cookies, local storage) on the
`chromium_profile` volume, so sessions survive restarts. The volume is in
the nightly backup (`/var/backups/personal-assistant/chromium-profile-*.tar.gz`).

## Diagnostics

    # from the host, on the aibrowse network, confirm Chrome answers:
    docker run --rm --network personal-assistant_aibrowse curlimages/curl:latest \
      -s http://172.28.0.10:9222/json/version

    # confirm the CDP port is NOT exposed on the host:
    ss -tlnp | grep 9222   # expect: no output
