import unittest

from app.services import login_rate_limit_service as service


class LoginRateLimitTests(unittest.TestCase):
    def setUp(self):
        service.reset_login_guard_for_tests()

    def tearDown(self):
        service.reset_login_guard_for_tests()

    def test_blocks_after_five_failures(self):
        for index in range(service.MAX_FAILED_ATTEMPTS - 1):
            self.assertFalse(
                service.record_login_failure("203.0.113.1", "Max", now=float(index))
            )
        self.assertTrue(
            service.record_login_failure("203.0.113.1", "Max", now=4.0)
        )
        self.assertTrue(service.is_login_blocked("203.0.113.1", "Max", now=5.0))

    def test_block_expires(self):
        for index in range(service.MAX_FAILED_ATTEMPTS):
            service.record_login_failure("203.0.113.1", "Max", now=float(index))
        self.assertFalse(
            service.is_login_blocked(
                "203.0.113.1",
                "Max",
                now=4.0 + service.BLOCK_SECONDS + 1,
            )
        )

    def test_success_clears_failures(self):
        for index in range(service.MAX_FAILED_ATTEMPTS - 1):
            service.record_login_failure("203.0.113.1", "Max", now=float(index))
        service.clear_login_failures("203.0.113.1", "Max")
        self.assertFalse(service.is_login_blocked("203.0.113.1", "Max", now=5.0))
        self.assertFalse(service.record_login_failure("203.0.113.1", "Max", now=6.0))

    def test_different_users_do_not_block_each_other(self):
        for index in range(service.MAX_FAILED_ATTEMPTS):
            service.record_login_failure("203.0.113.1", "Max", now=float(index))
        self.assertTrue(service.is_login_blocked("203.0.113.1", "Max", now=5.0))
        self.assertFalse(service.is_login_blocked("203.0.113.1", "Alex", now=5.0))

    def test_different_addresses_do_not_block_each_other(self):
        for index in range(service.MAX_FAILED_ATTEMPTS):
            service.record_login_failure("203.0.113.1", "Max", now=float(index))
        self.assertTrue(service.is_login_blocked("203.0.113.1", "Max", now=5.0))
        self.assertFalse(service.is_login_blocked("203.0.113.2", "Max", now=5.0))

    def test_old_failures_are_forgotten(self):
        service.record_login_failure("203.0.113.1", "Max", now=0.0)
        later = service.FAILURE_WINDOW_SECONDS + 1.0
        for offset in range(service.MAX_FAILED_ATTEMPTS - 1):
            self.assertFalse(
                service.record_login_failure(
                    "203.0.113.1",
                    "Max",
                    now=later + offset,
                )
            )
        self.assertFalse(
            service.is_login_blocked(
                "203.0.113.1",
                "Max",
                now=later + service.MAX_FAILED_ATTEMPTS,
            )
        )


if __name__ == "__main__":
    unittest.main()
