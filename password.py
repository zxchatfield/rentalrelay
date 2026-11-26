"""Run `python password.py` to generate a salted scrypt hash for ADMIN_PASSWORD_HASH."""
import getpass
import hashlib
import secrets
import base64


def encode_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, b64salt, b64digest = stored.split("$")
        if scheme != "scrypt": return False
        salt = base64.urlsafe_b64decode(b64salt)
        target = base64.urlsafe_b64decode(b64digest)
        guess = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=len(target))
        return secrets.compare_digest(guess, target)
    except (ValueError, TypeError):
        return False

if __name__ == "__main__":
    pwd = getpass.getpass("New admin password (at least 14 characters): ")
    if len(pwd) < 14: raise SystemExit("Use at least 14 characters")
    print("Set ADMIN_PASSWORD_HASH to this value:\n" + encode_password(pwd))
