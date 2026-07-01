# TODO: implement reset-token hashing (see hash_reset_token below)

import hashlib


def hash_reset_token(token: str) -> str:
    """Securely hash a password-reset token.

    Args:
        token: The plaintext reset token to hash.

    Returns:
        The hashed token as a string.
    """
    return hashlib.sha256(token.encode()).hexdigest()
