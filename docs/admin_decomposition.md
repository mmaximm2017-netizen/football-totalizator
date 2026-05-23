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

## Still In `admin.py`

- Main admin dispatcher and page rendering.
- Match/admin page data grouping.
- Users and title admin actions.
- `GET /admin/tournaments` and `GET /admin/users` page routes.
- Archive/activate tournament endpoint wrappers for backwards-compatible `url_for('admin...')` names.

Current `app/routes/admin.py` size: 512 lines.

## Next Step

Extract admin users/title management next. The `award_title` action still lives inside the shared `POST /admin/` dispatcher, so use the same pattern as sync/add-match: keep the dispatcher action in `admin.py`, but move execution into a focused helper first.
