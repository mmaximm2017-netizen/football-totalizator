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

## Still In `admin.py`

- Main admin dispatcher and page rendering.
- Match grouping and match admin helpers.
- Scoring recalculation admin routes.
- Users and title admin actions.
- Tournament admin routes.
- Team translation admin route.
- Debug match route.

## Next Step

Extract one isolated area at a time. The safest next candidate is tournament routes and tournament-only helpers, because they have clear URL boundaries and do not share the main `POST /admin/` dispatcher as heavily.
