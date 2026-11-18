"""TOTP code generation (RFC 6238). The seed never leaves this module's call stack.

Done by hand rather than with pyotp: pyotp 2.12 refuses seeds shorter than
112 bits, and many sites still issue 80-bit ones."""
import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, urlparse

_ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def _parse(seed: str) -> tuple[str, int, int, str]:
    """Return (base32 secret, digits, period, algorithm) from a bare seed or
    an otpauth:// URI, both of which Bitwarden stores in the totp field."""
    if seed.startswith("otpauth://"):
        q = {k: v[0] for k, v in parse_qs(urlparse(seed).query).items()}
        return (
            q.get("secret", ""),
            int(q.get("digits", 6)),
            int(q.get("period", 30)),
            q.get("algorithm", "SHA1").upper(),
        )
    return seed, 6, 30, "SHA1"


def generate_code(seed: str, now: float | None = None) -> str:
    secret, digits, period, algorithm = _parse(seed.strip())
    secret = secret.replace(" ", "").upper()
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if now is None else now) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), _ALGORITHMS[algorithm]).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)
