from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from login_broker import browser
from login_broker.browser import perform_login, _type_into, _login_confirmed


def _page(url="https://example.com/login"):
    page = AsyncMock()
    page.url = url
    return page


def _visible_pass_field(form):
    """A password ElementHandle mock that reports itself visible and whose
    closest('form') resolves — via evaluate_handle().as_element() — to `form`
    (or None for a form-less login widget). as_element() is synchronous in
    Playwright, so it is a plain MagicMock, not an AsyncMock."""
    pf = AsyncMock()
    pf.is_visible = AsyncMock(return_value=True)
    handle = MagicMock()
    handle.as_element = MagicMock(return_value=form)
    pf.evaluate_handle = AsyncMock(return_value=handle)
    return pf


@pytest.mark.asyncio
async def test_perform_login_fills_and_submits_without_returning_secrets():
    page = _page()
    # A visible password field is already on the page, so no modal is opened.
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())  # username + submit
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    assert "s3cr3t-pw" not in str(result)
    page.goto.assert_awaited_with("https://example.com/login")


@pytest.mark.asyncio
async def test_perform_login_allows_www_apex_redirect():
    # Requested the apex; the site redirected to its www host. Same site,
    # so the login must proceed (not abort).
    page = _page(url="https://www.example.com/login")
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    assert "s3cr3t-pw" not in str(result)


@pytest.mark.asyncio
async def test_perform_login_aborts_on_cross_origin_redirect():
    page = _page(url="https://evil.com/login")
    page.query_selector = AsyncMock()
    page.query_selector_all = AsyncMock()
    page.wait_for_selector = AsyncMock()
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is False
    assert "origin" in result["reason"]
    # nothing was inspected or typed on the wrong origin
    page.query_selector.assert_not_called()
    page.wait_for_selector.assert_not_called()
    assert "s3cr3t-pw" not in str(result)


@pytest.mark.asyncio
async def test_perform_login_opens_modal_when_no_password_field_yet():
    page = _page()
    trigger = AsyncMock()
    trigger.inner_text = AsyncMock(return_value="Sign in")
    # Guard finds no password field at all, so it clicks a login trigger.
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[trigger])
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    trigger.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_perform_login_opens_modal_when_password_present_but_hidden():
    # example-shop case: the password field is already in the DOM but sits in
    # a display:none modal. Presence must NOT short-circuit the trigger click;
    # only a *visible* field does. Old code returned early here and never
    # opened the modal, so the form was reported "not found".
    page = _page()
    hidden_pass = AsyncMock()
    hidden_pass.is_visible = AsyncMock(return_value=False)
    trigger = AsyncMock()
    trigger.inner_text = AsyncMock(return_value="Click here to login")
    page.query_selector = AsyncMock(return_value=hidden_pass)
    page.query_selector_all = AsyncMock(return_value=[trigger])
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/",
            username="99123456", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    trigger.click.assert_awaited_once()
    assert "s3cr3t-pw" not in str(result)


@pytest.mark.asyncio
async def test_perform_login_skips_hidden_trigger_and_clicks_visible_one():
    # example-shop case: the page carries duplicate "Click here to login"
    # triggers (a desktop copy and a mobile copy), and responsive CSS hides the
    # one that does not match the viewport. The desktop copy comes first in the
    # DOM but is not visible at the sidecar's width; inner_text still returns its
    # text, so a text match alone would pick it — and clicking a hidden element
    # makes Playwright wait 30s and time out, surfacing as a generic failure.
    # Only the visible copy may be clicked.
    page = _page()
    hidden_pass = AsyncMock()
    hidden_pass.is_visible = AsyncMock(return_value=False)
    hidden_trigger = AsyncMock()
    hidden_trigger.inner_text = AsyncMock(return_value="Click here to login")
    hidden_trigger.is_visible = AsyncMock(return_value=False)
    visible_trigger = AsyncMock()
    visible_trigger.inner_text = AsyncMock(return_value="Click here to login")
    visible_trigger.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=hidden_pass)
    page.query_selector_all = AsyncMock(return_value=[hidden_trigger, visible_trigger])
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/",
            username="99123456", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    hidden_trigger.click.assert_not_awaited()   # never click a hidden trigger
    visible_trigger.click.assert_awaited_once()  # the visible copy opens the modal
    assert "s3cr3t-pw" not in str(result)


@pytest.mark.asyncio
async def test_perform_login_scopes_fields_to_form_and_falls_back_to_bare_button():
    # example-shop case: the username is a mobile-number field inside the login
    # <form>, while a site-search text box sits earlier in the page. The lookup
    # must be scoped to the password field's form (not the page), and the login
    # button carries no type=submit, so submit falls back to the form's button.
    page = _page()
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)  # guard only
    form = AsyncMock()
    user_field = AsyncMock()
    bare_button = AsyncMock()
    # Inside the form: username -> user_field; typed-submit -> None; button -> bare_button
    form.query_selector = AsyncMock(side_effect=[user_field, None, bare_button])
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/",
            username="99123456", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    user_field.fill.assert_any_await("99123456")  # typed into the form's field
    bare_button.click.assert_awaited_once()        # bare form button submitted
    assert "s3cr3t-pw" not in str(result)


@pytest.mark.asyncio
async def test_perform_login_reports_when_form_never_appears():
    page = _page()
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.wait_for_selector = AsyncMock(side_effect=RuntimeError("timeout"))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222",
            url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is False
    assert result["reason"] == "login form not found"


def _verify_page(pass_field, triggers):
    """A page mock for _login_confirmed: a stable URL (so the settle loop reads
    two equal URLs and stops), the settled-page password field, and the list of
    candidate login triggers."""
    page = AsyncMock()
    page.url = "https://site.example/after"
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.query_selector = AsyncMock(return_value=pass_field)
    page.query_selector_all = AsyncMock(return_value=triggers)
    return page


def _trigger(text, visible):
    t = AsyncMock()
    t.inner_text = AsyncMock(return_value=text)
    t.is_visible = AsyncMock(return_value=visible)
    return t


@pytest.mark.asyncio
async def test_login_confirmed_true_when_no_login_ui_remains():
    # Settled page: no visible password field, no visible login trigger -> success.
    assert await _login_confirmed(_verify_page(None, [])) is True


@pytest.mark.asyncio
async def test_login_confirmed_false_when_password_field_still_visible():
    # A visible password field on the settled page means we are still logged out.
    still_pass = AsyncMock()
    still_pass.is_visible = AsyncMock(return_value=True)
    assert await _login_confirmed(_verify_page(still_pass, [])) is False


@pytest.mark.asyncio
async def test_login_confirmed_false_when_login_trigger_still_visible():
    # No password field, but a visible "Click here to login" remains -> still logged out.
    page = _verify_page(None, [_trigger("Click here to login", True)])
    assert await _login_confirmed(page) is False


@pytest.mark.asyncio
async def test_login_confirmed_ignores_hidden_or_non_login_triggers():
    # A hidden login trigger and a visible non-login button must not fail the check.
    page = _verify_page(None, [_trigger("Click here to login", False), _trigger("Add to cart", True)])
    assert await _login_confirmed(page) is True


@pytest.mark.asyncio
async def test_perform_login_reports_unconfirmed_when_form_stays_up():
    # Wrong password: submit is clicked, but the login form is still visible
    # afterwards -> must report failure, not the old false success.
    page = _page()
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)  # guard + verify
    page.query_selector_all = AsyncMock(return_value=[])
    page.wait_for_load_state = AsyncMock()
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://example.com/login",
            username="the user", password="wrong-pw", totp_code=None,
        )
    assert result["ok"] is False
    assert result["reason"] == "login not confirmed"
    assert "wrong-pw" not in str(result)


@pytest.mark.asyncio
async def test_perform_login_clears_stale_site_cookies_before_navigating():
    # A stale session in the shared profile can hide the login form. perform_login
    # must drop this site's cookies (every domain they are scoped to) before it
    # navigates, so the form renders fresh.
    page = _page(url="https://www.example-shop.com/")
    ctx = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[
        {"domain": ".example-shop.com"}, {"domain": "www.example-shop.com"}])
    ctx.clear_cookies = AsyncMock()
    page.context = ctx
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://www.example-shop.com/",
            username="99123456", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    cleared = {c.kwargs.get("domain") for c in ctx.clear_cookies.await_args_list}
    assert cleared == {".example-shop.com", "www.example-shop.com"}
    # cookies dropped before the page was navigated
    ctx.cookies.assert_awaited()
    page.goto.assert_awaited_with("https://www.example-shop.com/")


@pytest.mark.asyncio
async def test_type_into_falls_back_to_sequential_typing():
    field = AsyncMock()
    # fill() leaves a value that does not match -> sequential typing kicks in
    field.input_value = AsyncMock(return_value="")
    await _type_into(field, "1234")
    field.fill.assert_any_await("1234")
    field.type.assert_awaited_with("1234")


@pytest.mark.asyncio
async def test_type_into_recovers_when_fill_raises_on_number_input():
    # input[type=number] raises "Cannot type text into input[type=number]" when
    # the value has spaces/"+". The raise must not abort login — fall back to
    # typing (the second fill("") succeeds and clears the field).
    field = AsyncMock()
    field.fill = AsyncMock(side_effect=[RuntimeError("Cannot type text into input[type=number]"), None])
    await _type_into(field, "99123456")
    field.type.assert_awaited_with("99123456")


# A leaked tab is not a cosmetic untidiness: Playwright's connect_over_cdp
# auto-attaches to EVERY target in the shared browser, so one tab whose
# renderer has gone unresponsive stalls the whole connect until it times out.
# Observed on the production browser, which had accumulated 8 abandoned tabs,
# 5 of them wedged -> every browser_login raised and was reported as the
# generic "login failed". The page must be closed on every exit path.
@pytest.mark.asyncio
async def test_perform_login_closes_its_page_on_success():
    page = _page()
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    page.close.assert_awaited()


@pytest.mark.asyncio
async def test_perform_login_closes_its_page_when_the_form_is_never_found():
    page = _page()
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.wait_for_selector = AsyncMock(side_effect=RuntimeError("timeout"))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["reason"] == "login form not found"
    page.close.assert_awaited()


@pytest.mark.asyncio
async def test_perform_login_closes_its_page_when_the_origin_check_aborts():
    page = _page(url="https://evil.example.net/login")
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://example.com/login",
            username="the user", password="s3cr3t-pw", totp_code=None,
        )
    assert result["reason"] == "page redirected to a different origin"
    page.close.assert_awaited()


@pytest.mark.asyncio
async def test_perform_login_closes_its_page_when_the_login_raises():
    # Typing can raise (a hidden field never becomes actionable). The tab must
    # not survive the exception, or a failing site poisons the shared browser.
    page = _page()
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    page.query_selector = AsyncMock(return_value=visible_pass)
    form = AsyncMock()
    form.query_selector = AsyncMock(return_value=AsyncMock())
    page.wait_for_selector = AsyncMock(return_value=_visible_pass_field(form))
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._type_into",
               AsyncMock(side_effect=RuntimeError("element is not visible"))):
        with pytest.raises(RuntimeError):
            await perform_login(
                cdp_url="http://172.28.0.10:9222", url="https://example.com/login",
                username="the user", password="s3cr3t-pw", totp_code=None,
            )
    page.close.assert_awaited()


@pytest.mark.asyncio
async def test_connect_page_bounds_the_cdp_connect_timeout():
    # Playwright's default is 180s. A wedged tab then hangs browser_login for
    # three minutes before the caller sees a generic failure; bound it so the
    # attempt fails while the agent (and its user) is still waiting.
    chromium = AsyncMock()
    chromium.connect_over_cdp = AsyncMock(return_value=AsyncMock(contexts=[AsyncMock()]))
    pw = AsyncMock()
    pw.chromium = chromium
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    with patch("login_broker.browser.async_playwright", MagicMock(return_value=starter)), \
         patch("login_broker.browser.sweep_unresponsive_targets", MagicMock(return_value=0)):
        await browser._connect_page("http://172.28.0.10:9222")
    _, kwargs = chromium.connect_over_cdp.call_args
    assert kwargs.get("timeout") == browser.CDP_CONNECT_TIMEOUT_MS
    assert browser.CDP_CONNECT_TIMEOUT_MS <= 30000


def _fake_playwright():
    chromium = AsyncMock()
    chromium.connect_over_cdp = AsyncMock(return_value=AsyncMock(contexts=[AsyncMock()]))
    pw = AsyncMock()
    pw.chromium = chromium
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    return starter, chromium


@pytest.mark.asyncio
async def test_connect_page_sweeps_dead_tabs_before_attaching():
    # Order matters: connect_over_cdp is the call that hangs on a wedged tab,
    # so sweeping afterwards would never run.
    calls = []
    starter, chromium = _fake_playwright()
    chromium.connect_over_cdp = AsyncMock(
        side_effect=lambda *a, **k: calls.append("connect")
        or AsyncMock(contexts=[AsyncMock()]))
    with patch("login_broker.browser.async_playwright", MagicMock(return_value=starter)), \
         patch("login_broker.browser.sweep_unresponsive_targets",
               MagicMock(side_effect=lambda url: calls.append(f"sweep:{url}") or 0)):
        await browser._connect_page("http://172.28.0.10:9222")
    assert calls == ["sweep:http://172.28.0.10:9222", "connect"]


@pytest.mark.asyncio
async def test_connect_page_still_attaches_when_the_sweep_blows_up():
    # The sweep is a courtesy. If it fails, the login must still be attempted —
    # otherwise a helper meant to fix logins becomes a new way to break them.
    starter, chromium = _fake_playwright()
    with patch("login_broker.browser.async_playwright", MagicMock(return_value=starter)), \
         patch("login_broker.browser.sweep_unresponsive_targets",
               MagicMock(side_effect=OSError("cdp endpoint refused"))):
        await browser._connect_page("http://172.28.0.10:9222")
    chromium.connect_over_cdp.assert_awaited()


def _formless_page(buttons):
    """A form-less (SPA) login widget: no <form>, no type=submit, only bare
    buttons. page.query_selector answers by selector."""
    page = _page("https://ticktick.com/signin")
    visible_pass = AsyncMock()
    visible_pass.is_visible = AsyncMock(return_value=True)
    user_field = AsyncMock()

    async def query_selector(selector):
        if selector == browser.PASSWORD_SELECTOR:
            return visible_pass
        if selector == browser.USERNAME_SELECTOR:
            return user_field
        return None  # no type=submit, no TOTP field

    page.query_selector = AsyncMock(side_effect=query_selector)
    page.query_selector_all = AsyncMock(return_value=buttons)
    pass_field = _visible_pass_field(None)
    page.wait_for_selector = AsyncMock(return_value=pass_field)
    return page, pass_field


def _button(text, visible=True):
    b = AsyncMock()
    b.inner_text = AsyncMock(return_value=text)
    b.is_visible = AsyncMock(return_value=visible)
    return b


@pytest.mark.asyncio
async def test_formless_widget_clicks_the_sign_in_button_not_sign_up():
    # TickTick case: no <form>, no type=submit, a "Sign Up" and a "Sign In"
    # button side by side. The old code found no submit and clicked nothing,
    # so the login was never sent and came back "login not confirmed".
    sign_up, sign_in = _button("Sign Up"), _button("Sign In")
    page, _ = _formless_page([sign_up, sign_in])
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        result = await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://ticktick.com/signin",
            username="me@example.com", password="s3cr3t-pw", totp_code=None,
        )
    assert result["ok"] is True
    sign_in.click.assert_awaited_once()
    sign_up.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_formless_widget_without_a_sign_in_button_presses_enter():
    page, pass_field = _formless_page([_button("Sign Up"), _button("Sign In", visible=False)])
    with patch("login_broker.browser._connect_page", AsyncMock(return_value=page)), \
         patch("login_broker.browser._login_confirmed", AsyncMock(return_value=True)):
        await perform_login(
            cdp_url="http://172.28.0.10:9222", url="https://ticktick.com/signin",
            username="me@example.com", password="s3cr3t-pw", totp_code=None,
        )
    pass_field.press.assert_awaited_once_with("Enter")
