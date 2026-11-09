import pyotp
from login_broker.totp import generate_code

def test_generate_code_matches_pyotp_reference():
    seed = pyotp.random_base32()
    expected = pyotp.TOTP(seed).now()
    assert generate_code(seed) == expected

def test_generate_code_returns_six_digits():
    seed = pyotp.random_base32()
    code = generate_code(seed)
    assert len(code) == 6
    assert code.isdigit()
