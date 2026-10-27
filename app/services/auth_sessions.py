"""Revocable browser sessions. Only token digests are stored in PostgreSQL."""
import hashlib
import secrets


def token_digest(token):
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def create_session(cur, user_id):
    token = secrets.token_urlsafe(32)
    cur.execute("DELETE FROM auth_sessions WHERE expires_at <= NOW()")
    cur.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, NOW() + INTERVAL '7 days')",
        (token_digest(token), user_id),
    )
    return token


def revoke_session(cur, token):
    cur.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_digest(token),))
