from login_broker.totp import generate_code

# RFC 6238 appendix B: the ASCII secret "12345678901234567890" (SHA-1).
RFC_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238_vectors():
    assert generate_code(RFC_SEED, now=59) == "287082"
    assert generate_code(RFC_SEED, now=1111111109) == "081804"
    assert generate_code(RFC_SEED, now=2000000000) == "279037"


def test_80_bit_seed_is_accepted():
    # pyotp 2.12 refuses seeds under 112 bits; many sites still issue
    # 16-character (80-bit) ones, and those must keep working.
    code = generate_code("JBSWY3DPEHPK3PXP", now=0)
    assert len(code) == 6 and code.isdigit()


def test_seed_formatting_is_tolerated():
    assert generate_code("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", now=59) == "287082"


def test_otpauth_uri_parameters_are_honoured():
    uri = f"otpauth://totp/Example:me?secret={RFC_SEED}&issuer=Example&digits=8&period=30"
    assert generate_code(uri, now=59) == "94287082"


def test_otpauth_sha256():
    # RFC 6238 SHA-256 secret is the 32-byte ASCII "1234567890" * 3 + "12".
    import base64
    seed = base64.b32encode(b"12345678901234567890123456789012").decode()
    uri = f"otpauth://totp/x?secret={seed}&algorithm=SHA256&digits=8"
    assert generate_code(uri, now=59) == "46119246"
