# Telegram Mini App

Таблицы результатов в приложении показываются целиком, без разбиения участников
на страницы. Пояснения к расчётам не сокращают число видимых участников.
Лимиты длины сообщений и пагинация текстового Telegram-рендерера сохраняются.

The existing Flask application serves `/telegram-app` and its JSON endpoint
`POST /telegram-app/api`. No separate hosting, AI provider, database schema or
package is needed in production. The Mini App is an admin analytics viewer;
forecast submission and account linking are outside this release.

## Launch and configuration

After an authorized deployment, `/start` or the existing main menu in a private
bot chat includes **Открыть приложение** pointing to
`https://totish.ru/telegram-app`. Inline web_app launch needs no BotFather
configuration. The bot menu keeps the app launcher, question help and administration. Retired
analytics buttons in old bot messages offer the app launcher locally without a
helper/database read. Existing question commands and administrative navigation
remain available. Shared screen renderers are retained for the Mini App.
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

The Mini App has one «Матчи» tab for schedules, results and forecasts. The
`matches` API action accepts only the tournament kind and an optional validated
round number. It reads the existing `gpt_safe.calendar_rounds` classification;
national-team and Supercup fixtures remain outside rounds. The current/upcoming
round opens by default, or the last round after the season. The selector and
arrows navigate whole rounds, without Telegram text-page limits or match caps.

Collapsed fixtures use a compact layout tested with eight matches at 360×800 and
320×740. A finished fixture expands to its score and a participant / forecast /
stored points table. Only one result stays open. Expanded results, large rounds,
large accessibility fonts, or very long team names may require vertical scrolling.

Finished status aliases and complete valid scores use the canonical scoring
validator. Future kickoffs, missing/future deadlines, live and scheduled matches
never include results, predictions or absence data in this response. A single
batched query reads forecasts for eligible finished IDs only, scoped to their
tournament. The read-only transaction uses a repeatable-read snapshot so scores
and stored points cannot come from different versions of a result update. Null
points stay «—», zero stays 0; missing forecasts are explicit. No points are
recalculated, no new background polling or database writes are introduced.

Existing bot/chat prediction screens retain their deadline policy. Participant
forecast buttons in the Mini App lead to the combined matches tab. All names and
values use DOM text nodes, never HTML interpolation.

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

## Round summaries

The «Итоги тура» button next to refresh opens totals for the selected round,
including past rounds, without adding height to the fixture list. A separate
signed `round_summary` action reads only on demand and uses the same short
read-only connection and repeatable-read snapshot. A return button restores the
selected round. Refresh bypasses the existing 30-second in-memory cache.

Round totals sum stored points for valid finished, played, post-deadline matches
in that calendar round. A partially played round is explicitly preliminary and
has no winner labels. Missing or invalid stored points in completed fixtures
produce an awaiting-scoring state rather than zero totals or premature winners.
Shared leaders by round points are all winners once the round is fully finished;
a round without scored forecasts has no winner.

Overall places are reconstructed immediately before the first kickoff and after
the last kickoff of the selected round, bounded by now. They include tournament
fixtures in those chronological windows (including simultaneous kickoffs), and
exclude matches after the selected round. The canonical overall tie-break order
is points, exact scores (10/11), exact differences (7/8), outcomes (3), username.
The calculation uses current results and the active roster from `gpt_safe.users`,
not historical membership or result-finalization snapshots. Deleted identities
are not reconstructed. The UI explains this in «Как считается». If no standings
existed before the round, or earlier points are still pending, movement is left
unknown instead of inventing a previous place. No scoring, schema, roster or
production-data changes are made by this feature.


## Participant links

Participant nicknames in the standings, finished-match forecasts and round
summaries open the existing read-only profile by authoritative user ID within
the selected tournament. Mini App ranking responses attach place/name/ID
metadata from the same ordered ranking read (`with_players=True`); ordinary
bot output remains unchanged. Finished-match forecast rows now carry the ID
already present in the user join, and round summaries reuse their existing IDs.
Names are never used to guess identity or construct HTML. Missing or malformed
IDs remain plain text. No extra participant lookup query is added to list reads.

The profile offers an explicit return to its source view, including after a
load error. Telegram Back also returns when there is no inner profile history.
The original tab, tournament, selected round, open match, scroll position and
nickname focus are restored. An automatically selected round is pinned to its
actual number for the return trip. State and profile cache remain in memory;
manual tab/tournament changes discard the return context. Existing profile
actions, archive restrictions, launch validation and forecast privacy gates are
unchanged. No database writes, account linking or public access is introduced.


## Analytics and participant presentation

Statistics use a compact list of metrics. Round wins and exact-score counts have
relative bars; the numeric value remains authoritative, zero stays zero, and
missing results have no fabricated bar. Results are not split across pages.

Mini App participant cards include a structured `profile` alongside the existing
text and navigation. The profile reuses the safe tournament ranking for place,
points, exact scores and average points per scored forecast. Completed-round
wins reuse the assistant calculation with an additional bound user-ID filter
applied after ranking all participants. Up to five recent scored or pending
forecasts are read only for valid finished matches after both kickoff and deadline.
A single repeatable-read transaction covers the card. Archived participants get
no round-win or detailed-result reads. Null values remain unavailable, not zero.
No new API action, database write, grant or migration is introduced.

Final round summaries highlight every joint winner. Preliminary, pending and
empty summaries do not show a winner banner. Place movement retains the existing
before/after calculation and caveats; labels and arrows accompany the colours.


## Navigation, inline periods and legacy participant picker

The bottom navigation contains only standings, matches and statistics. Profiles
open from nicknames in the standings, finished matches and round summaries;
the originating tab stays active. Explicit/Telegram Back restores the source
view, selected round, open match, scroll and focus. Legacy picker navigation
returns to the standings rather than opening a duplicate directory.

For compatibility, the existing card picker still accepts `with_players=True`.
It returns a `players` list with authoritative ID, canonical place and points
from one safe ranking read. No per-player lookup or profile-detail reads are
performed for this legacy response. An unscored tournament has no fabricated places;
an empty roster gets a dedicated empty state. The bot picker retains pagination.

Statistics open directly with results. All-tournament, round and date controls
stay above the result; the applied period remains visible while another period
is being edited. Applying dates uses the existing validated date callback,
collapses the editor and preserves values for refresh/retry. Recent selections
are kept only in memory, bounded to 40 tournament/metric/player keys. The round
selector uses the existing read-only callback and returns all rounds for the
Mini App without Telegram message pagination. It makes no background polling
requests. Late/aborted picker responses cannot cross navigation or tournaments.

Loading placeholders match participant/result lists or match rows. Empty states
keep the actual reason, including pending round points. Request errors expose
an explicit retry on the same callback and period; a picker failure leaves the
existing result in place. App-launch authorization and database privacy rules
remain unchanged. No new endpoint, migration or production write is introduced.

## Tournament palettes inherited from the site

The Mini App reuses the site's tournament identity, rather than assigning new
unrelated colours. `templates/table.html` and `templates/base.html` supply RPL's
blue canvas/hero (`#001B4D`, `#0066D6`, `#0B5CC9`) and red detail (`#D52B1E`).
`static/css/tournaments/russian-cup.css` supplies the Cup's plum canvas
(`#260521`), red hero (`#B7002B` to `#E80024`), lime (`#C7F21A`) and dark surfaces.
Cup light mode adapts this identity to white data cards, and dark mode uses plum
cards and lime numeric accents. Text colours are adjusted for readable contrast.

`data-tournament` and `data-theme` on the root element drive CSS tokens for the
canvas, surfaces, text, headers, profile/match hero, comparisons and primary
buttons. Navigation and profile return synchronize the palette before rendering
results, including cached/error states. Telegram header/background/bottom-bar
colours and the browser theme-colour meta tag follow the same tokens. Success,
place movement and medal meanings remain separate from tournament identity.
Only the existing appearance preference is stored; no data, requests, scoring,
authorization or match visibility rules change.

The browser suite checks both tournaments in both appearance modes, major
text/background contrast pairs (at least 4.5:1), Telegram colour calls, Cup hero
colour, and the existing compact 320/360px navigation/profile/period scenarios.
