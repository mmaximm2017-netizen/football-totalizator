from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "app/db.py"
text = path.read_text(encoding="utf-8-sig")
start = text.index("def init_db():\n")
end = text.index("\n\n# =========================================================\n# ACTIVE TOURNAMENT", start)
new_block = '''def init_db():
    """Seed bootstrap data after versioned migrations have created the schema."""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SET lock_timeout = '5s';")
        cur.execute("SET statement_timeout = '30s';")

        cur.execute("SELECT id FROM tournaments LIMIT 1")
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO tournaments (name, is_active, start_date)
                VALUES ('Кубок Матч-премьер', 1, '2026-05-06')
                """
            )

        seed_russian_cup_tournament(cur)

        cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO users (username, password, is_admin)
                VALUES (%s, %s, 1)
                """,
                (ADMIN_USERNAME, build_bootstrap_admin_password_hash()),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)
'''
path.write_text(text[:start] + new_block + text[end:], encoding="utf-8")

# Add an explicit contract test: init_db must never become a schema migration path again.
test_path = ROOT / "tests/test_db_initialization.py"
test = test_path.read_text(encoding="utf-8")
anchor = '''    def test_init_db_only_seeds_default_tournament_for_an_empty_table(self):
        source = inspect.getsource(db.init_db)

        self.assertIn("SELECT id FROM tournaments LIMIT 1", source)
        self.assertNotIn("WHERE is_active = 1 LIMIT 1", source)
        self.assertNotIn("Курбок Матч-премьер", source)
'''
replacement = anchor + '''
    def test_init_db_does_not_modify_schema_or_backfill_existing_rows(self):
        source = inspect.getsource(db.init_db).upper()

        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("CREATE INDEX", source)
        self.assertNotIn("DROP INDEX", source)
        self.assertNotIn("UPDATE MATCHES", source)
        self.assertNotIn("UPDATE USERS", source)
'''
if anchor not in test:
    raise RuntimeError("test anchor not found")
test_path.write_text(test.replace(anchor, replacement, 1), encoding="utf-8")

print("init_db reduced to data bootstrap only")
