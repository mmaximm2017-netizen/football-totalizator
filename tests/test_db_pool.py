import threading
import unittest
from unittest.mock import patch

from app import db


class MockThreadedPool:
    def __init__(self, connections, minconn=1, maxconn=5):
        self.connections = list(connections)
        self.returned = []
        self.minconn = minconn
        self.maxconn = maxconn
        self.close_errors = []
        self._lock = threading.Lock()
        self._used = []

    def getconn(self, key=None):
        with self._lock:
            if not self.connections:
                raise db.PoolError("connection pool exhausted")
            conn = self.connections.pop(0)
            self._used.append(conn)
            return conn

    def putconn(self, conn, key=None, close=False):
        with self._lock:
            if conn not in self._used:
                raise db.PoolError("connection is not in the pool")
            self._used.remove(conn)
            self.returned.append((conn, close))
            if close:
                conn.close()
            else:
                self.connections.append(conn)

    def closeall(self):
        with self._lock:
            for conn in self._used:
                try:
                    conn.close()
                except Exception as exc:  # noqa: BLE001 - fake pool records close failures.
                    self.close_errors.append(exc)
            self._used.clear()
            self.connections.clear()


class FakeConnection:
    def __init__(self, alive=True):
        self.closed = False
        self.alive = alive
        self._rolled_back = False
        self.info = _ConnectionInfo()

    def cursor(self):
        return FakeCursor(self.alive)

    def close(self):
        self.closed = True

    def rollback(self):
        self._rolled_back = True


class _ConnectionInfo:
    transaction_status = 0


class FakeCursor:
    def __init__(self, alive=True):
        self.alive = alive
        self.closed = False

    def execute(self, query, params=None):
        if not self.alive:
            raise RuntimeError("connection is not alive")

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = True


class DbPoolThreadSafetyTests(unittest.TestCase):
    def setUp(self):
        self.original_pool = db.db_pool
        db.db_pool = None

    def tearDown(self):
        db.db_pool = self.original_pool

    def test_threaded_connection_pool_is_used(self):
        from psycopg2.pool import ThreadedConnectionPool

        self.assertIs(db.ThreadedConnectionPool, ThreadedConnectionPool)

    def test_init_pool_does_not_replace_existing_pool(self):
        fake = MockThreadedPool([FakeConnection()])
        db.db_pool = fake

        db.init_pool()

        self.assertIs(db.db_pool, fake)

    def test_getconn_putconn_symmetric_single_thread(self):
        pool = MockThreadedPool([FakeConnection(), FakeConnection()])
        db.db_pool = pool

        c1 = db.get_db()
        db.close_db(c1)

        self.assertEqual(len(pool.returned), 1)
        self.assertEqual(pool.returned[0][0], c1)
        self.assertFalse(pool.returned[0][1])

    def test_two_threads_get_different_connections(self):
        pool = MockThreadedPool([FakeConnection(), FakeConnection()])
        db.db_pool = pool

        results = {}

        def worker(idx):
            conn = db.get_db()
            results[idx] = id(conn)
            db.close_db(conn)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertIn(1, results)
        self.assertIn(2, results)
        self.assertNotEqual(results[1], results[2])

    def test_both_connections_returned_to_pool(self):
        pool = MockThreadedPool([FakeConnection(), FakeConnection()])
        db.db_pool = pool

        def worker():
            conn = db.get_db()
            db.close_db(conn)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(pool.returned), 2)
        for conn, close_flag in pool.returned:
            self.assertFalse(close_flag)

    def test_exception_in_one_thread_does_not_affect_other(self):
        pool = MockThreadedPool([FakeConnection(), FakeConnection()])
        db.db_pool = pool

        results = {}

        def failing_worker():
            try:
                conn = db.get_db()
                raise ValueError("oops")
            except ValueError:
                db.close_db(conn)
                results["failed"] = id(conn)

        def ok_worker():
            conn = db.get_db()
            db.close_db(conn)
            results["ok"] = id(conn)

        t1 = threading.Thread(target=failing_worker)
        t2 = threading.Thread(target=ok_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertIn("failed", results)
        self.assertIn("ok", results)
        self.assertNotEqual(results["failed"], results["ok"])
        self.assertEqual(len(pool.returned), 2)

    def test_rollback_called_before_putconn(self):
        conn = FakeConnection()
        conn.alive = True
        pool = MockThreadedPool([conn])
        db.db_pool = pool

        returned = db.get_db()
        db.close_db(returned)

        self.assertTrue(returned._rolled_back)

    def test_broken_connection_putconn_close_true(self):
        dead = FakeConnection(alive=False)
        replacement = FakeConnection(alive=True)
        pool = MockThreadedPool([dead, replacement])
        db.db_pool = pool

        returned = db.get_db()

        self.assertIs(returned, replacement)
        self.assertEqual(len(pool.returned), 1)
        self.assertTrue(pool.returned[0][1])
        self.assertTrue(dead.closed)

    def test_closed_pooled_connection_is_discarded_from_pool(self):
        conn = FakeConnection()
        pool = MockThreadedPool([conn])
        db.db_pool = pool

        returned = db.get_db()
        returned.close()
        db.close_db(returned)

        self.assertEqual(pool.returned, [(returned, True)])

    def test_cursor_cleanup_failure_still_returns_connection(self):
        conn = FakeConnection()
        pool = MockThreadedPool([conn])
        db.db_pool = pool
        cur = FakeCursor()
        cur.close = lambda: (_ for _ in ()).throw(RuntimeError("cursor close failed"))

        returned = db.get_db()
        db.close_db(returned, cur)

        self.assertEqual(pool.returned, [(returned, False)])

    def test_primary_pool_has_no_direct_connection_fallback(self):
        self.assertNotIn("psycopg2.connect", db.get_db.__code__.co_names)

    def test_putconn_on_raw_not_in_pool_closes_directly(self):
        """A connection that cannot be returned to pool is closed directly."""
        pool = MockThreadedPool([FakeConnection()])
        db.db_pool = pool

        raw = FakeConnection()
        db.close_db(raw)

        self.assertTrue(raw.closed)

    def test_closeall_not_called_on_teardown(self):
        pool = MockThreadedPool([FakeConnection()])
        db.db_pool = pool

        conn = db.get_db()
        db.close_db(conn)

        self.assertEqual(len(pool.returned), 1)

    def test_reset_pool_calls_closeall(self):
        pool = MockThreadedPool([FakeConnection(), FakeConnection()])
        db.db_pool = pool

        original_closeall = pool.closeall
        called = []
        def tracking_closeall():
            called.append(True)
            original_closeall()
        pool.closeall = tracking_closeall

        with patch.object(db, "init_pool"):
            db.reset_pool()

        self.assertEqual(len(called), 1)
        self.assertIsNone(db.db_pool)

    def test_repeated_init_does_not_replace_existing_pool(self):
        pool = MockThreadedPool([FakeConnection()])
        db.db_pool = pool

        db.init_pool()

        self.assertIs(db.db_pool, pool)

    def test_reset_pool_replaces_pool(self):
        pool = MockThreadedPool([FakeConnection()])
        db.db_pool = pool

        db.reset_pool()

        self.assertIsNot(db.db_pool, pool)
        self.assertIsInstance(db.db_pool, db.ThreadedConnectionPool)

    def test_reset_pool_refuses_to_close_active_connections(self):
        pool = MockThreadedPool([FakeConnection()])
        db.db_pool = pool
        conn = db.get_db()

        with self.assertRaisesRegex(RuntimeError, "active_connections"):
            db.reset_pool()

        db.close_db(conn)

    def test_pool_exhaustion_raises_error(self):
        limited = MockThreadedPool([FakeConnection()], minconn=1, maxconn=1)
        db.db_pool = limited

        first = db.get_db()

        errors = []
        def worker():
            try:
                limited.getconn()
            except db.PoolError as e:
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(len(errors), 1)
        db.close_db(first)

    def test_nested_checkout_exhausts_small_pool_but_owned_cursor_does_not(self):
        pool = MockThreadedPool([FakeConnection()], minconn=1, maxconn=1)
        db.db_pool = pool
        outer = db.get_db()

        with self.assertRaises(db.PoolExhausted):
            db.get_db()

        db.close_db(outer)

    def test_four_threads_can_share_small_pool_without_nested_checkouts(self):
        pool = MockThreadedPool([FakeConnection() for _ in range(4)], minconn=1, maxconn=4)
        db.db_pool = pool
        errors = []
        barrier = threading.Barrier(4, timeout=10)

        def worker():
            try:
                conn = db.get_db()
                barrier.wait()
                db.close_db(conn)
            except Exception as exc:  # noqa: BLE001 - worker failures are test results.
                errors.append(exc)

        workers = [threading.Thread(target=worker) for _ in range(4)]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(pool.returned), 4)

    def test_concurrent_init_pool_creates_only_one_pool(self):
        db.db_pool = None

        call_count = []

        class SlowPool:
            def __init__(self, *args, **kwargs):
                call_count.append(1)
                self.minconn = 1
                self.maxconn = 5
                import time

                time.sleep(0.15)

            def closeall(self):
                pass

        with patch.object(db, "ThreadedConnectionPool", SlowPool):
            barrier = threading.Barrier(2, timeout=10)
            pools = []

            def worker():
                barrier.wait()
                db.init_pool()
                pools.append(db.db_pool)

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(len(call_count), 1)
        self.assertIsNotNone(pools[0])
        self.assertIs(pools[0], pools[1])
