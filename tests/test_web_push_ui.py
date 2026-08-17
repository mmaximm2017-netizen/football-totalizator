import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WebPushUiTests(unittest.TestCase):
    def test_push_controls_are_only_rendered_for_own_profile(self):
        template = (ROOT / "templates" / "profile.html").read_text(encoding="utf-8")
        self.assertIn('{% if is_own_profile %}\n    <section class="section-card web-push-card" data-web-push', template)
        self.assertIn('data-subscribe-url="{{ url_for(\'push.subscribe\') }}"', template)
        self.assertIn('data-unsubscribe-url="{{ url_for(\'push.unsubscribe\') }}"', template)
        self.assertIn('data-test-url="{{ url_for(\'push.test_push\') }}"', template)
        self.assertIn("web-push.js", template)

    def test_client_uses_existing_api_and_csrf_header(self):
        script = (ROOT / "static" / "js" / "web-push.js").read_text(encoding="utf-8")
        for value in (
            "Notification.requestPermission()",
            "registration.pushManager.getSubscription()",
            "registration.pushManager.subscribe({",
            "applicationServerKey",
            "X-CSRF-Token",
            "card.dataset.subscribeUrl",
            "card.dataset.unsubscribeUrl",
            "card.dataset.testUrl",
            "subscription.unsubscribe()",
        ):
            self.assertIn(value, script)
        self.assertIn("toggle.addEventListener('change'", script)
        self.assertIn("toggle.checked", script)
        self.assertIn("setState('active')", script)
        self.assertIn("setState('inactive')", script)
        self.assertIn("setState('denied')", script)
        self.assertIn("setState('unsupported')", script)
        self.assertIn("setState('ios-guidance')", script)
        self.assertIn("setBusy(true)", script)
        self.assertIn("testButton.addEventListener('click', sendTest)", script)
        self.assertIn("async function enable()", script)
        self.assertIn("async function disable()", script)
        self.assertIn("body: '{}'", script)

    def test_profile_uses_one_accessible_notification_toggle(self):
        template = (ROOT / "templates" / "profile.html").read_text(encoding="utf-8")
        self.assertIn('data-push-toggle', template)
        self.assertIn('role="switch"', template)
        self.assertIn('class="push-toggle-track"', template)
        self.assertNotIn('data-push-enable', template)
        self.assertNotIn('data-push-disable', template)
        self.assertNotIn('Включить уведомления</button>', template)
        self.assertNotIn('Отключить уведомления</button>', template)
        self.assertIn("v='20260817-notification-toggle'", template)

    def test_foreign_profile_has_no_push_controls(self):
        template = (ROOT / "templates" / "profile.html").read_text(encoding="utf-8")
        self.assertIn("{% if is_own_profile %}", template)
        self.assertIn("data-web-push", template)


if __name__ == "__main__":
    unittest.main()
