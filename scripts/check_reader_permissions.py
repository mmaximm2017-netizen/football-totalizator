#!/usr/bin/env python3
"""Read-only release check for the *actual* GPT connection role."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gpt_db import short_gpt_read


def check_reader(cur):
    cur.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = CURRENT_USER")
    flags = cur.fetchone()
    if not flags or any(flags):
        raise RuntimeError("reader_role_is_privileged")
    cur.execute("""SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relkind IN ('r','v','m','p','f')
                   AND (has_table_privilege(c.oid, 'SELECT') OR has_table_privilege(c.oid, 'INSERT')
                        OR has_table_privilege(c.oid, 'UPDATE') OR has_table_privilege(c.oid, 'DELETE'))""")
    if cur.fetchone()[0]:
        raise RuntimeError("reader_has_public_data_access")
    cur.execute("SELECT COUNT(*) FROM gpt_safe.users")
    cur.fetchone()


if __name__ == "__main__":
    try:
        with short_gpt_read() as cursor:
            check_reader(cursor)
        print("GPT READER PERMISSIONS OK")
    except Exception:
        print("GPT READER PERMISSIONS FAILED: check role grants; no credentials printed")
        raise SystemExit(1)
