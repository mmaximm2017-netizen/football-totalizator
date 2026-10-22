# Telegram Mini App

The existing Flask application serves `/telegram-app` and its JSON endpoint
`POST /telegram-app/api`. No separate hosting, AI provider, database schema or
package is needed in production. The Mini App is an admin analytics viewer;
forecast submission and account linking are outside this release.

## Launch and configuration

After an authorized deployment, `/start` or the existing main menu in a private
bot chat includes **Открыть приложение** pointing to
`https://totish.ru/telegram-app`. Inline web_app launch needs no BotFather
configuration. Existing commands and inline navigation remain available.
The launch button is omitted when TELEGRAM_ERROR_CHAT_ID is absent or is a
group ID. Obtain a fresh menu message after deployment; Telegram cannot update
previously sent keyboards automatically.

The Flask container must receive the same existing values used by the bot:

- TELEGRAM_ERROR_BOT_TOKEN: verifies Telegram initData HMAC on the server.
- TELEGRAM_ADMIN_USER_ID: the single authorized Telegram user (not chat ID).
- TOTISH_GPT_DATABASE_URL: existing isolated read-only data connection.
- SECRET_KEY: existing application key, also signs short-lived question context.

Docker Compose already reads the production .env. No token or database URL is
sent to the browser. Missing configuration fails closed. Future public access
requires an explicit identity/account-linking design; do not remove the admin
check to make this version public.

## Data and lifecycle

The shell and local navigation do not access Postgres, including when an old
website session cookie is present. The API ignores website sessions and checks
Telegram HMAC, duplicate fields, signed user identity, and a one-hour launch TTL
before data access. Requests carry initData in Authorization, never in a URL.
Reopen the Mini App after the launch expires.

The API delegates to existing telegram_bot_ui_service and telegram_sql_assistant
queries. Their tournament filters, finished-match rules, read-only role and
forecast deadline gates remain authoritative. No arbitrary SQL or host
administration action is exposed. All API responses use no-store, sanitized
errors and a bounded per-worker limit of 60 authorized requests/minute. With
multiple Gunicorn workers this is a per-worker burst guard, not a global quota.

The browser has a bounded, memory-only, 30-second screen cache. Refresh bypasses
it; no background polling, analytics trackers, or persistent forecast cache are
added. Tab changes cancel stale requests. The blue/white light theme is the
default; the header toggle selects a navy dark theme. Only that appearance
preference is saved in localStorage; launch data, questions and results remain
in memory. Assets are versioned by TOTISH_RELEASE. Telegram SDK source
and validation scheme: https://core.telegram.org/bots/webapps.

Question follow-ups use a signed, user-bound, 15-minute context token carried
in memory, so a request reaching another Gunicorn worker preserves the parser
context. The existing question parser remains deterministic and free of paid
AI calls. Context is integrity protected, not encrypted; it contains the
question filters already visible to the authorized user.

## Result presentation

The browser recognizes the existing fixed text formats without another database
query: schedules become fixture cards grouped by Moscow calendar day, and each
prediction list stays under its own fixture. Missing forecasts and pre-deadline
notices remain explicit. A page starting midway through a forecast list keeps
its original text rather than attributing rows to a guessed match.

Points, round wins, averages, exact scores and streaks use labeled value columns.
Explanations are kept in the expandable «Как считается» section; streak caveats
and missing scored forecasts remain attached to the correct participant. Unknown
or invalid formats fall back to the original text. All names and values use DOM
text nodes, never HTML interpolation.

## Verification and release

Run tests/test_telegram_mini_app.py and the existing Telegram tests. The CI UI
job starts a database-free shell and exercises mobile UI using clearly defined
fixture API responses, not production records. It checks navigation, custom
dates and refresh, question follow-ups, safe rendering of user strings, light
and dark themes, retry behavior, and stale-request cancellation. Screenshots
are uploaded as mini-app-browser-screenshots.

The existing release workflow checks app health, exact image release and loaded
host bot module hashes. The first end-to-end Telegram launch with real signed
initData still needs verification after an explicitly authorized deployment.
