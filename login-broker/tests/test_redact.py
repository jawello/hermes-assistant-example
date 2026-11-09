from login_broker.redact import mask_username, assert_no_secret_leak

def test_mask_username_short():
    assert mask_username("al") == "**"

def test_mask_username_normal():
    assert mask_username("the user") == "th******"

def test_assert_no_secret_leak_passes_clean_payload():
    assert_no_secret_leak({"ok": True, "site": "example.com"}, secrets=["s3cr3t", "123456"])

def test_assert_no_secret_leak_raises_on_password_in_payload():
    import pytest
    with pytest.raises(ValueError):
        assert_no_secret_leak({"ok": False, "detail": "wrong s3cr3t"}, secrets=["s3cr3t"])

def test_assert_no_secret_leak_raises_on_totp_in_nested_payload():
    import pytest
    with pytest.raises(ValueError):
        assert_no_secret_leak({"ok": True, "meta": {"code": "123456"}}, secrets=["123456"])
