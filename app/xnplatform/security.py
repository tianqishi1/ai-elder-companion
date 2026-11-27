"""认证原语：PBKDF2 口令哈希、令牌、CSRF（CR-02/03）。"""
import hashlib, hmac, secrets

def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${dk.hex()}"

def verify_password(pw, stored):
    try:
        _, salt, hexdk = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
        return hmac.compare_digest(dk.hex(), hexdk)
    except Exception:
        return False

def new_token():
    return secrets.token_hex(24)