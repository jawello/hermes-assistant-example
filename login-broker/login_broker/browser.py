"""Drives the shared headless-Chromium container over CDP to submit a login
form. Credentials are passed in as plain function args and used exactly once
via Playwright; they are never logged or returned. Handles forms that appear
behind a modal (clicks a login trigger, then waits for the field) — including
modals whose fields are already in the DOM but hidden (display:none) until the
trigger is clicked — mobile-number logins (input[type=number/tel]), forms whose
submit button carries no type=submit, and fields that ignore fill()
(role=spinbutton one-time-code inputs -> sequential type)."""
import asyncio
import sys

from playwright.async_api import async_playwright

from login_broker.cdp import sweep_unresponsive_targets
from login_broker.matching import hosts_match

USERNAME_SELECTOR = (
    "input[type=email], input[name=username], input[type=tel], "
    "input[type=number], input[type=text], [role=textbox]"
)
PASSWORD_SELECTOR = "input[type=password], input[autocomplete=current-password]"
SUBMIT_SELECTOR = "button[type=submit], input[type=submit]"
TOTP_SELECTOR = (
    "input[name=otp], input[name=totp], input[autocomplete=one-time-code], "
    "input[inputmode=numeric], [role=spinbutton]"
)
LOGIN_TRIGGER_SELECTOR = "button, a, [role=button]"
LOGIN_TRIGGER_TEXTS = ("sign in", "log in", "login")
FORM_TIMEOUT_MS = 5000
# Per-navigation wait while settling after submit. Kept short because a submit
# can chain a POST and a delayed redirect; _login_confirmed polls until the URL
# stops changing rather than trusting a single load event.
SETTLE_TIMEOUT_MS = 2000
# Ceiling on settle iterations (~SETTLE_ROUNDS * (SETTLE_TIMEOUT_MS + 700ms)).
SETTLE_ROUNDS = 15
# Playwright's own default here is 180s. Attaching has to reach every target
# in the shared browser, so an unresponsive tab hangs the whole connect —
# and three minutes of silence surfaces as an unexplained "login failed"
# long after the agent gave up. Fail fast instead: a healthy attach to a
# local container takes well under a second.
CDP_CONNECT_TIMEOUT_MS = 15000


async def _connect_page(cdp_url: str):
    """Open a fresh tab in the shared browser, clearing dead tabs out of the way
    first. The sweep has to come before connect_over_cdp, because attaching is
    the step a wedged tab hangs; sweeping afterwards would never be reached. It
    is best-effort by design — a sweep that cannot run must not be a new reason
    a login fails. Blocking sockets, so it goes to a thread."""
    try:
        closed = await asyncio.to_thread(sweep_unresponsive_targets, cdp_url)
        if closed:
            print(f"cdp sweep: closed {closed} unresponsive tab(s) before attaching",
                  file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"cdp sweep skipped ({type(exc).__name__})", file=sys.stderr, flush=True)
    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(
        cdp_url, timeout=CDP_CONNECT_TIMEOUT_MS)
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    return await context.new_page()


async def _type_into(field, value: str) -> None:
    """Enter a value into a field, degrading to sequential typing when fill()
    does not take. fill() fails two ways: it may return without the value
    sticking (some role=spinbutton one-time-code inputs), or it may *raise* —
    an input[type=number] (e.g. a "Mobile Number" login field) throws "Cannot
    type text into input[type=number]" when the value carries spaces or a "+".
    Typing character by character recovers both cases; number inputs silently
    drop characters they can't accept, so the digits still go in."""
    try:
        await field.fill(value)
    except Exception:
        pass  # e.g. input[type=number] rejected the raw value -> type it
    else:
        try:
            if await field.input_value() == value:
                return
        except Exception:
            return  # input_value unsupported here; assume fill() worked
    try:
        await field.fill("")
    except Exception:
        pass
    await field.type(value)


async def _open_login_form(page) -> None:
    """If no *visible* password field is present yet, click a login trigger to
    reveal a modal login form. The field may already be in the DOM but hidden
    (a display:none modal), so presence alone is not enough — checking
    visibility is what makes such modals open. Best-effort — a missing trigger
    just leaves the page as is and the caller's wait times out.

    Only a *visible* trigger is clicked. Responsive sites ship duplicate
    triggers (a desktop copy and a mobile copy of the same "Click here to
    login" link) and hide the one that does not match the viewport; the hidden
    copy often still reports text via inner_text, so a text match alone is not
    enough. Clicking a hidden trigger makes Playwright wait for it to become
    actionable and then time out after 30s, which surfaces as a generic
    "login failed" — so hidden candidates must be skipped, not clicked."""
    field = await page.query_selector(PASSWORD_SELECTOR)
    if field and await field.is_visible():
        return
    for trigger in await page.query_selector_all(LOGIN_TRIGGER_SELECTOR):
        text = ((await trigger.inner_text()) or "").strip().lower()
        if any(t in text for t in LOGIN_TRIGGER_TEXTS) and await trigger.is_visible():
            await trigger.click()
            return


async def _visible_login_button(page):
    """The visible button whose whole label is a login word ("Sign In").
    Exact match, not substring: "Sign in with Google" starts an OAuth flow
    rather than submitting the typed credentials."""
    for button in await page.query_selector_all(LOGIN_TRIGGER_SELECTOR):
        text = ((await button.inner_text()) or "").strip().lower()
        if text in LOGIN_TRIGGER_TEXTS and await button.is_visible():
            return button
    return None


async def _login_confirmed(page) -> bool:
    """Decide whether a submitted login actually took. Clicking submit is not
    proof: rejected credentials leave the site logged out, which previously
    still returned success.

    A submit commonly chains a POST and then a (sometimes delayed) redirect, so
    do not trust a single load event — reading an intermediate page wrongly
    looks like success. Poll until the URL stops changing, then judge the
    settled page. It is NOT a confirmed login if the settled page still offers a
    way to log in:

    - the password field is visible, or
    - a visible log-in trigger (`sign in` / `log in` / `login`) remains.

    Either means we are still logged out (verified on example-shop.com, whose
    failed login settles on an error page that still shows the login form and a
    `Click here to login` link). A successful login replaces that with the
    logged-in UI. Redirect and cookie state are deliberately not used as
    signals — both change on a *failed* login too. `load`, not `networkidle`,
    which never fires on sites with persistent trackers; reads are retried
    because a late navigation can destroy the execution context mid-read."""
    stable = None
    for _ in range(SETTLE_ROUNDS):
        try:
            await page.wait_for_load_state("load", timeout=SETTLE_TIMEOUT_MS)
        except Exception:
            pass  # already loaded, or a slow tracker
        try:
            url = page.url
            if url == stable:  # two equal reads in a row -> navigation settled
                pass_field = await page.query_selector(PASSWORD_SELECTOR)
                if pass_field and await pass_field.is_visible():
                    return False
                for trig in await page.query_selector_all(LOGIN_TRIGGER_SELECTOR):
                    text = ((await trig.inner_text()) or "").strip().lower()
                    if any(t in text for t in LOGIN_TRIGGER_TEXTS) and await trig.is_visible():
                        return False
                return True
            stable = url
        except Exception:
            pass  # a late navigation destroyed the context; settle and retry
        await page.wait_for_timeout(700)
    return True


async def _reset_site_cookies(page, url: str) -> None:
    """Drop this site's cookies from the (shared, persistent) browser profile
    before logging in. A stale or half-valid session left in the profile can
    make the server render a logged-in header with no "log in" trigger, so the
    login form never opens and login fails with "login form not found" (seen on
    example-shop.com, where leftover session cookies removed the "Click here to
    login" link entirely). Starting clean brings the form back; a fresh, valid
    session is written on success. Only this site's cookies are touched; other
    sites in the shared profile are left alone. Best-effort."""
    try:
        ctx = page.context
        for domain in {c["domain"] for c in await ctx.cookies(url)}:
            await ctx.clear_cookies(domain=domain)
    except Exception:
        pass


async def _do_login(page, url: str, username: str, password: str,
                    totp_code: str | None) -> dict:
    await _reset_site_cookies(page, url)
    await page.goto(url)
    # Defense-in-depth: only type credentials if the page actually landed on
    # the host the vault entry was selected for. A www<->apex redirect is the
    # same site and is allowed; any other origin change aborts.
    if not hosts_match(url, page.url):
        return {"ok": False, "reason": "page redirected to a different origin"}
    await _open_login_form(page)
    try:
        pass_field = await page.wait_for_selector(PASSWORD_SELECTOR, timeout=FORM_TIMEOUT_MS)
    except Exception:
        return {"ok": False, "reason": "login form not found"}
    if not pass_field:
        return {"ok": False, "reason": "login form not found"}
    # Scope the username/submit lookup to the password field's own <form> so we
    # don't grab an unrelated input that happens to match the generic username
    # selector (e.g. a site-search box that sits earlier in the DOM). Falls
    # back to the whole page for form-less (SPA-style) login widgets.
    form = (await pass_field.evaluate_handle("el => el.closest('form')")).as_element()
    scope = form or page
    user_field = await scope.query_selector(USERNAME_SELECTOR)
    if not user_field:
        return {"ok": False, "reason": "login form not found"}
    await _type_into(user_field, username)
    await _type_into(pass_field, password)
    submit = await scope.query_selector(SUBMIT_SELECTOR)
    if submit is None and form is not None:
        # A form's implicit submit button often carries no type=submit; inside
        # a form the first button is the one that submits it.
        submit = await form.query_selector("button")
    if submit is None and form is None:
        # A form-less widget (TickTick) has neither: pick the visible button
        # labelled as the login, never its "Sign Up" neighbour.
        submit = await _visible_login_button(page)
    if submit:
        await submit.click()
    else:
        # Nothing to click means nothing was sent; Enter in the password
        # field is how such widgets submit from the keyboard.
        await pass_field.press("Enter")
    if totp_code:
        totp_field = await page.query_selector(TOTP_SELECTOR)
        if totp_field:
            await _type_into(totp_field, totp_code)
            totp_submit = await page.query_selector(SUBMIT_SELECTOR)
            if totp_submit:
                await totp_submit.click()
    if await _login_confirmed(page):
        return {"ok": True}
    return {"ok": False, "reason": "login not confirmed"}


async def perform_login(cdp_url: str, url: str, username: str, password: str,
                        totp_code: str | None) -> dict:
    """Log in on `url` in a fresh tab of the shared browser, and always close
    that tab again.

    Closing is not tidiness. connect_over_cdp auto-attaches to every target in
    the browser, so a single abandoned tab whose renderer has stopped
    responding stalls the *next* connect until it times out — one leak makes
    every later browser_login fail. Production reached 8 abandoned tabs, 5 of
    them wedged, and every login then returned the generic "login failed",
    which reads exactly like rejected credentials. Hence `finally`, which also
    covers the raising paths (an unactionable field, a navigation that kills
    the context)."""
    page = await _connect_page(cdp_url)
    try:
        return await _do_login(page, url, username, password, totp_code)
    finally:
        try:
            await page.close()
        except Exception:
            pass  # already gone with its context; nothing left to leak
