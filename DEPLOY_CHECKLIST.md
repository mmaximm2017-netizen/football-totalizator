# Deploy Checklist

Practical pre/post deploy checks for Render.

## 1) Before push

1. Run `git status` and ensure there are no accidental files.
2. Review critical changes in `app/routes`, `app/services`, and `templates`.
3. Run smoke check:
   - `python scripts/smoke_check.py`
   - or `py -3 scripts/smoke_check.py`

## 2) Push and deploy

1. Push branch: `git push origin <branch>`
2. Wait for successful Render deploy.
3. Check startup logs for errors/tracebacks.

## 3) Health checks

1. `GET /health` should return `{"status":"ok"}`
2. `GET /health/db` should report:
   - `db=ok`
   - `active_tournament=ok`
   - `ranking=ok`
3. If `single_active` is not `ok`, verify tournament active-state before continuing.

## 4) Functional smoke (manual)

1. Login works.
2. Logout (POST) works.
3. Leaderboard opens and shows data.
4. Profile opens and shows data.
5. Admin pages open:
   - `/admin`
   - `/admin/matches`
   - `/admin/tournaments`
   - `/admin/users`

## 5) Tournament safety

1. Confirm active tournament exists.
2. Confirm there is exactly one active tournament.
3. If active-state is inconsistent, pause tournament-related deploys until fixed.
