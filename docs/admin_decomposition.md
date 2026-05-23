# Admin Decomposition

This tracks small steps toward reducing `app/routes/admin.py` without changing admin behavior.

## Moved Out

### Sync

- `GET /admin/sync-health` now lives in `app/routes/admin_sync.py` on `admin_sync_bp`.
- Sync panel data preparation now lives in `build_sync_panel_context()`.
- Sync panel status formatting now lives in `_format_minutes_ago()` and `_build_sync_panel_view()`.
- Manual admin sync execution now lives in `handle_manual_sync_update()`.

The existing manual sync form still posts to `POST /admin/` with `action=update_matches`. That shared admin dispatcher remains in `admin.py` because the same URL still handles non-sync admin actions.

### Manual Match Management

- `POST /admin/force_finish` now lives in `app/routes/admin_matches.py` on `admin_matches_bp`.
- `POST /admin/fix_result` now lives in `app/routes/admin_matches.py`.
- `POST /admin/edit_match` now lives in `app/routes/admin_matches.py`.
- `POST /admin/delete_match` now lives in `app/routes/admin_matches.py`.
- `add_match` and `set_result` execution now live in `handle_add_match()` and `handle_set_result()`.
- Match validation and manual deadline helpers now live in `admin_matches.py`.

The existing add-match and set-result forms still post to `POST /admin/` with action values, so the shared dispatcher remains in `admin.py` and delegates to the extracted helpers.

### Tournament Management And Admin Utilities

- `POST /admin/new_tournament` now lives in `app/routes/admin_tournaments.py` on `admin_tournaments_bp`.
- `POST /admin/delete_tournament` now lives in `app/routes/admin_tournaments.py`.
- Tournament archive/activate DB operations now live in `handle_archive_tournament()` and `handle_activate_tournament()`.
- `POST /admin/recalc_all` now lives in `app/routes/admin_tournaments.py`.
- `POST /admin/translate` now lives in `app/routes/admin_tournaments.py`.
- `POST /admin/debug_match` now lives in `app/routes/admin_tournaments.py`.

The existing tournament template calls `url_for('admin.archive_tournament')` and `url_for('admin.activate_tournament')`, so those endpoint wrappers remain in `admin.py` and delegate to the extracted helpers. This preserves templates and endpoint names.

### Shared `POST /admin/` Actions

- Action dispatch now lives in `app/routes/admin_actions.py`.
- `ACTION_HANDLERS` maps action names to thin execution functions.
- `dispatch_admin_action()` handles unknown actions and keeps the existing redirect/flash behavior.
- Connected actions:
  - `update_matches`
  - `add_match`
  - `set_result`
  - `award_title`

`POST /admin/`, `request.form["action"]`, forms, templates, and redirects are unchanged. `admin.py` now only reads the action and calls the dispatcher.

### Admin View Preparation

- Admin page data preparation now lives in `app/services/admin_view_service.py`.
- Moved functions:
  - `prepare_admin_view_data()`
  - `prepare_admin_matches_data()`
  - `normalize_league_key()`
- Match grouping, tournament/user lists, active tournament selection for forms, and template context assembly were moved without changing SQL queries or template keys.

### Shared Admin Infrastructure

- Shared admin infrastructure now lives in `app/routes/admin_common.py`.
- `admin_required` is imported from `admin_common.py` by:
  - `app/routes/admin.py`
  - `app/routes/admin_sync.py`
  - `app/routes/admin_matches.py`
  - `app/routes/admin_tournaments.py`
- The decorator behavior is unchanged: it checks `session["user_id"]`, queries `users.is_admin`, flashes the same access error, and redirects to the existing auth/main endpoints.

## Current Structure

- `admin.py`: main admin page routes, GET rendering orchestration, compatibility wrappers.
- `admin_actions.py`: shared `POST /admin/` action registry and dispatch.
- `admin_sync.py`: sync health route, sync panel context, manual sync execution.
- `admin_matches.py`: manual match management routes and helpers.
- `admin_tournaments.py`: tournament routes and simple admin utility actions.
- `admin_common.py`: shared admin route infrastructure.
- `admin_view_service.py`: admin template context preparation.

## Still In `admin.py`

- Main admin route, page rendering, and one-line POST action dispatch call.
- `GET /admin/tournaments` and `GET /admin/users` page routes.
- Archive/activate tournament endpoint wrappers for backwards-compatible `url_for('admin...')` names.

Current `app/routes/admin.py` size: 104 lines.

## Remaining Technical Debt

- Some compatibility wrappers remain in `admin.py` because templates call `url_for('admin.archive_tournament')` and `url_for('admin.activate_tournament')`.
- GET page routes still live together in `admin.py`; they are now thin, but could be split later if template endpoint names are preserved.
- `admin_view_service.py` still prepares broad shared context for several pages; a later cleanup could split page-specific context builders after behavior is covered by tests.
