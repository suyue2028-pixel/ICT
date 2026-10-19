"""Salted scrypt password storage using the existing Python standard library."""
import hashlib
import secrets
import threading
import unicodedata

MIN_LENGTH, MAX_LENGTH = 15, 128
N, R, P = 2**17, 8, 1
_slots = threading.BoundedSemaphore(2)


class PasswordBusy(Exception):
    pass


def normalize_password(value):
    return unicodedata.normalize("NFC", value)


def _derive(password, salt):
    if not _slots.acquire(blocking=False):
        raise PasswordBusy()
    try:
        return hashlib.scrypt(normalize_password(password).encode("utf-8"), salt=salt,
            n=N, r=R, p=P, dklen=32, maxmem=256 * 1024 * 1024)
    finally:
        _slots.release()


def hash_password(password):
    salt = secrets.token_bytes(16)
    return f"scrypt${N}${R}${P}${salt.hex()}${_derive(password, salt).hex()}"


def verify_password(password, encoded):
    try:
        kind, n, r, p, salt, digest = encoded.split("$")
        if (kind, n, r, p) != ("scrypt", str(N), str(R), str(P)) or len(salt) != 32 or len(digest) != 64:
            return False
        return secrets.compare_digest(_derive(password, bytes.fromhex(salt)), bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


# Unknown accounts still perform the same expensive password operation. This
# hash is never an account credential and is not used to issue any session.
DUMMY_HASH = hash_password("non-account timing placeholder")
