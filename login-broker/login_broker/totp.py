"""TOTP code generation. The seed never leaves this module's call stack."""
import pyotp


def generate_code(seed: str) -> str:
    return pyotp.TOTP(seed).now()
