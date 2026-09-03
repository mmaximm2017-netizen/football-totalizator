import inspect
import unittest
from unittest.mock import Mock, patch

from werkzeug.security import check_password_hash

from app import db


class Connection:
    def __init__(self, closed=False):
        self.closed = closed
        self.queries = []
        self._rolled_back = False
        self.info = _ConnectionInfo()

    def cursor(self):
        return Cursor(self.queries)

    def close(self):
        self.closed = True

    def rollback(self):
        self._rolled_back = True


class _ConnectionInfo:
    transaction_status = 0


class Cursor:
    def __init__(self, queries):
        self.queries = queries
        self.closed = False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = True


class Pool:
    def __init__(self, connections):
        self.connections = list(connections)
        self.returned = []

    def getconn(self):
        return self.connections.pop(0)

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


class TournamentCursor:
    def __init__(self, tournaments):
        self.tournaments = tournaments
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if "INSERT INTO tournaments" in query and params:
            name = params[0]
            if not any(row["name"] == name for row in self.tournaments):
                self.tournaments.append({"name": name, "is_active": 1})


class DbInitializationTests(unittest.TestCase):
    def setUp(self):
        self.original_pool = db.db_pool
        db.db_pool = None

    def tearDown(self):
        db.db_pool = self.original_pool

    def test_get_db_only_checks_connection_and_never_runs_initialization(self):
        conn = Connection()
        pool = Pool([conn])
        db.db_pool = pool

        with patch.object(db, "init_db") as init_db, patch.object(
            db, "seed_russian_cup_tournament"
        ) as seed:
            returned = db.get_db()

        self.assertIs(returned, conn)
        self.assertEqual(len(conn.queries), 1)
        self.assertIn("SELECT 1", conn.queries[0][0])
        self.assertNotIn("ALTER TABLE", conn.queries[0][0])
        self.assertNotIn("tournaments", conn.queries[0][0].lower())
        init_db.assert_not_called()
        seed.assert_not_called()

    def test_archived_russian_cup_stays_archived_across_get_db_calls(self):
        archived_cup = {"name": db.RUSSIAN_CUP_TOURNAMENT_NAME, "is_active": 0}
        first, second = Connection(), Connection()
        db.db_pool = Pool([first, second])

        db.get_db()
        db.get_db()

        self.assertEqual(archived_cup["is_active"], 0)
        for conn in (first, second):
            self.assertTrue(all("tournaments" not in query.lower() for query, _ in conn.queries))

    def test_controlled_seed_preserves_existing_archived_russian_cup(self):
        tournaments = [{"name": db.RUSSIAN_CUP_TOURNAMENT_NAME, "is_active": 0}]
        cur = TournamentCursor(tournaments)

        db.seed_russian_cup_tournament(cur)

        self.assertEqual(tournaments, [{"name": db.RUSSIAN_CUP_TOURNAMENT_NAME, "is_active": 0}])
        self.assertEqual(len(tournaments), 1)
        self.assertFalse(any("UPDATE tournaments" in query for query, _ in cur.queries))

    def test_controlled_seed_creates_missing_russian_cup_once(self):
        tournaments = []
        cur = TournamentCursor(tournaments)

        db.seed_russian_cup_tournament(cur)
        db.seed_russian_cup_tournament(cur)

        self.assertEqual(tournaments, [{"name": db.RUSSIAN_CUP_TOURNAMENT_NAME, "is_active": 1}])

    def test_bootstrap_admin_password_is_hashed_before_storage(self):
        password_hash = db.build_bootstrap_admin_password_hash()

        self.assertNotEqual(password_hash, db.ADMIN_PASSWORD)
        self.assertTrue(check_password_hash(password_hash, db.ADMIN_PASSWORD))

    def test_init_db_runs_russian_cup_seed_only_through_controlled_initialization(self):
        conn = Mock()
        cur = Mock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = (1,)

        with (
            patch.object(db, "get_db", return_value=conn),
            patch.object(db, "close_db"),
            patch.object(db, "seed_russian_cup_tournament") as seed,
        ):
            db.init_db()

        seed.assert_called_once_with(cur)
        conn.commit.assert_called_once()

    def test_init_db_only_seeds_default_tournament_for_an_empty_table(self):
        source = inspect.getsource(db.init_db)

        self.assertIn("SELECT id FROM tournaments LIMIT 1", source)
        self.assertNotIn("WHERE is_active = 1 LIMIT 1", source)
        self.assertNotIn("Курбок Матч-премьер", source)

    def test_init_db_does_not_modify_schema_or_backfill_existing_rows(self):
        source = inspect.getsource(db.init_db).upper()

        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("CREATE INDEX", source)
        self.assertNotIn("DROP INDEX", source)
        self.assertNotIn("UPDATE MATCHES", source)
        self.assertNotIn("UPDATE USERS", source)

    def test_dead_connection_is_replaced_and_close_db_returns_connection(self):
        dead = Connection(closed=True)
        replacement = Connection()
        pool = Pool([dead, replacement])
        db.db_pool = pool

        returned = db.get_db()

        self.assertIs(returned, replacement)
        self.assertEqual(pool.returned, [(dead, True)])

        db.close_db(replacement)
        self.assertEqual(pool.returned[-1], (replacement, False))


if __name__ == "__main__":
    unittest.main()
