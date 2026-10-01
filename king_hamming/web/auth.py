"""Users, password hashing, sessions, CSRF tokens, and login throttling.

All state lives in a private directory outside the repository (default
~/.local/share/king_hamming/web): users.json holds scrypt password hashes and
roles; sessions.sqlite holds only SHA-256 digests of session tokens, so a copy
of the database cannot be replayed as a login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

ROLES = ("viewer", "operator")
SESSION_SECONDS = 7 * 24 * 3600
IDLE_SECONDS = 24 * 3600
REAUTH_SECONDS = 10 * 60
TOUCH_SECONDS = 60
MIN_PASSWORD = 10
SCRYPT = {"n": 2**15, "r": 8, "p": 1}
NAME_CHARACTERS = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
DEFAULT_STATE = Path.home() / ".local" / "share" / "king_hamming" / "web"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=128 * 1024**2, dklen=32, **SCRYPT)
    return "scrypt${n}${r}${p}${salt}${digest}".format(
        **SCRYPT, salt=base64.b64encode(salt).decode(), digest=base64.b64encode(digest).decode())


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), maxmem=128 * 1024**2, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, base64.b64decode(digest))


# Verifying against this for unknown names keeps response time independent of
# whether the account exists.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_name(name: str) -> str:
    if not name or len(name) > 64 or not set(name) <= NAME_CHARACTERS:
        raise ValueError("user names use 1-64 lowercase letters, digits, '.', '_' or '-'")
    return name


def check_password(name: str, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"passwords need at least {MIN_PASSWORD} characters")
    if password.lower() == name.lower():
        raise ValueError("the password must differ from the user name")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Throttle:
    """Slow repeated login failures per client address and per user name.

    The first four failures within 15 minutes are free; after that each one
    doubles a lockout, from 2 seconds up to 15 minutes.
    """

    WINDOW = 15 * 60
    FREE = 4
    MAXIMUM = 15 * 60

    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.failures: dict[str, list[float]] = {}
        self.blocked: dict[str, float] = {}

    def wait(self, *keys: str) -> float:
        """Seconds until every key may try again (0 when allowed now)."""
        now = self.clock()
        with self.lock:
            return max([0.0] + [self.blocked.get(key, 0) - now for key in keys])

    def failed(self, *keys: str) -> None:
        now = self.clock()
        with self.lock:
            if len(self.failures) > 10_000:
                self.failures = {key: times for key, times in self.failures.items()
                                 if times and times[-1] > now - self.WINDOW}
                self.blocked = {key: until for key, until in self.blocked.items() if until > now}
            for key in keys:
                times = [stamp for stamp in self.failures.get(key, []) if stamp > now - self.WINDOW]
                times.append(now)
                self.failures[key] = times
                excess = len(times) - self.FREE
                if excess > 0:
                    self.blocked[key] = now + min(self.MAXIMUM, 2.0 ** excess)

    def succeeded(self, *keys: str) -> None:
        with self.lock:
            for key in keys:
                self.failures.pop(key, None)
                self.blocked.pop(key, None)


class Auth:
    """Load users and manage sessions in one private state directory."""

    def __init__(self, state: Path = DEFAULT_STATE, clock=time.time) -> None:
        self.state = state
        self.clock = clock
        self.throttle = Throttle(clock)
        self.lock = threading.Lock()
        state.mkdir(parents=True, exist_ok=True)
        os.chmod(state, 0o700)
        self.users_path = state / "users.json"
        self.database = state / "sessions.sqlite"
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user TEXT NOT NULL, "
                "csrf TEXT NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL, "
                "reauth_at REAL, address TEXT, agent TEXT)")
        os.chmod(self.database, 0o600)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    # ----- users ---------------------------------------------------------

    def users(self) -> dict[str, dict]:
        if not self.users_path.exists():
            return {}
        return json.loads(self.users_path.read_text()).get("users", {})

    def save_users(self, users: dict[str, dict]) -> None:
        temporary = self.users_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"version": 1, "users": users}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        temporary.replace(self.users_path)

    def add_user(self, name: str, password: str, role: str = "viewer", replace: bool = False) -> None:
        check_name(name)
        check_password(name, password)
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        with self.lock:
            users = self.users()
            if name in users and not replace:
                raise ValueError(f"user {name} already exists")
            previous = users.get(name, {})
            users[name] = {"hash": hash_password(password), "role": role if not replace else previous.get("role", role),
                           "created": previous.get("created", self.clock()), "changed": self.clock()}
            self.save_users(users)
        if replace:
            self.revoke(name)

    def set_password(self, name: str, password: str) -> None:
        if name not in self.users():
            raise ValueError(f"no user {name}")
        self.add_user(name, password, replace=True)

    def set_role(self, name: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        with self.lock:
            users = self.users()
            if name not in users:
                raise ValueError(f"no user {name}")
            users[name]["role"] = role
            self.save_users(users)

    def remove_user(self, name: str) -> None:
        with self.lock:
            users = self.users()
            if users.pop(name, None) is None:
                raise ValueError(f"no user {name}")
            self.save_users(users)
        self.revoke(name)

    def verify(self, name: str, password: str) -> dict | None:
        """Return the user record when the password matches, else None."""
        record = self.users().get(name)
        valid = verify_password(password, record["hash"] if record else _DUMMY_HASH)
        return {"name": name, "role": record["role"]} if valid and record else None

    # ----- sessions ------------------------------------------------------

    def login(self, name: str, password: str, address: str, agent: str = "") -> tuple[str, dict] | None:
        user = self.verify(name, password)
        if user is None:
            return None
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE created<? OR last_seen<?",
                               (now - SESSION_SECONDS, now - IDLE_SECONDS))
            connection.execute(
                "INSERT INTO sessions(token_hash,user,csrf,created,last_seen,reauth_at,address,agent) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (token_digest(token), name, secrets.token_urlsafe(24), now, now, now, address, agent[:200]))
        return token, user

    def session(self, token: str | None) -> dict | None:
        """Return the live session for token, with the user's current role."""
        if not token:
            return None
        now = self.clock()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE token_hash=?",
                                     (token_digest(token),)).fetchone()
            if row is None:
                return None
            if row["created"] < now - SESSION_SECONDS or row["last_seen"] < now - IDLE_SECONDS:
                connection.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
                return None
            record = self.users().get(row["user"])
            if record is None:  # user removed
                connection.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
                return None
            if now - row["last_seen"] > TOUCH_SECONDS:
                connection.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?",
                                   (now, row["token_hash"]))
        return {"user": row["user"], "role": record["role"], "csrf": row["csrf"],
                "token_hash": row["token_hash"], "created": row["created"],
                "reauth_until": (row["reauth_at"] or 0) + REAUTH_SECONDS}

    def reauthenticate(self, session: dict, password: str) -> bool:
        if self.verify(session["user"], password) is None:
            return False
        with self.connect() as connection:
            connection.execute("UPDATE sessions SET reauth_at=? WHERE token_hash=?",
                               (self.clock(), session["token_hash"]))
        return True

    def recently_authenticated(self, session: dict) -> bool:
        return self.clock() < session["reauth_until"]

    def logout(self, session: dict) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (session["token_hash"],))

    def revoke(self, name: str) -> int:
        with self.connect() as connection:
            return connection.execute("DELETE FROM sessions WHERE user=?", (name,)).rowcount

    def sessions(self) -> list[dict]:
        with self.connect() as connection:
            return [{key: row[key] for key in ("user", "created", "last_seen", "address", "agent")}
                    for row in connection.execute("SELECT * FROM sessions ORDER BY last_seen DESC")]
