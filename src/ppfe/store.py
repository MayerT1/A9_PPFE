"""SQLite persistence: users + saved workspaces (projects).

Local default is a single file at data/ppfe.sqlite3. For a multi-user cloud
deployment, point PPFE_DATA_DIR at a persistent volume (or swap this module
for Postgres -- the interface is five functions).

Passwords: PBKDF2-HMAC-SHA256, 310k iterations, per-user salt (stdlib only).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT,
  pw_hash TEXT NOT NULL,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  state TEXT NOT NULL,          -- JSON: aoi, run_id, annotations, layers, routes, view
  created TEXT NOT NULL,
  updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    con = sqlite3.connect(get_settings().db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        with _lock:
            yield con
            con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript(SCHEMA)


# ------------------------------------------------------------------ users
def hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    _, salt, dk = stored.split("$")
    return hmac.compare_digest(hash_pw(pw, bytes.fromhex(salt)), stored)


def create_user(email: str, name: str, pw: str) -> dict:
    with db() as con:
        cur = con.execute("INSERT INTO users(email, name, pw_hash, created) VALUES (?,?,?,?)",
                          (email.lower().strip(), name.strip(), hash_pw(pw), _now()))
        return {"id": cur.lastrowid, "email": email.lower().strip(), "name": name.strip()}


def auth_user(email: str, pw: str) -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
    if r and check_pw(pw, r["pw_hash"]):
        return {"id": r["id"], "email": r["email"], "name": r["name"]}
    return None


def get_user(uid: int) -> dict | None:
    with db() as con:
        r = con.execute("SELECT id, email, name FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------- projects
def list_projects(uid: int) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT id, name, created, updated, state FROM projects WHERE user_id=? ORDER BY updated DESC",
                           (uid,)).fetchall()
    out = []
    for r in rows:
        st = json.loads(r["state"])
        out.append({"id": r["id"], "name": r["name"], "created": r["created"], "updated": r["updated"],
                    "aoi": (st.get("aoi") or {}).get("name"), "run_id": st.get("run_id"),
                    "n_annotations": len((st.get("annotations") or {}).get("features", []))})
    return out


def get_project(uid: int, pid: int) -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, uid)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "name": r["name"], "created": r["created"], "updated": r["updated"],
            "state": json.loads(r["state"])}


def save_project(uid: int, name: str, state: dict, pid: int | None = None) -> dict:
    now = _now()
    with db() as con:
        if pid:
            cur = con.execute("UPDATE projects SET name=?, state=?, updated=? WHERE id=? AND user_id=?",
                              (name, json.dumps(state), now, pid, uid))
            if cur.rowcount == 0:
                raise KeyError(pid)
        else:
            cur = con.execute("INSERT INTO projects(user_id, name, state, created, updated) VALUES (?,?,?,?,?)",
                              (uid, name, json.dumps(state), now, now))
            pid = cur.lastrowid
    return get_project(uid, pid)


def delete_project(uid: int, pid: int) -> bool:
    with db() as con:
        return con.execute("DELETE FROM projects WHERE id=? AND user_id=?", (pid, uid)).rowcount > 0
