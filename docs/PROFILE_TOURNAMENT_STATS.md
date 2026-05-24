# PROFILE_TOURNAMENT_STATS

## Implemented selected tournament filter

Profile statistics now use the same selected tournament id that the profile page already uses for ranking:

- `tournament_id = get_selected_tournament_id(request.args.get('tid', type=int))`

The profile route now filters these blocks by `p.tournament_id = %s`:

- aggregate finished prediction stats;
- total points;
- average points;
- total finished bets;
- exact scores, exact diffs, outcomes, close misses, misses;
- recent finished predictions list.

The profile place was already tournament-scoped through `get_tournament_ranking(tournament_id)` and was not changed.

Old predictions with `p.tournament_id IS NULL` are intentionally not mixed into a selected tournament. They can be handled later only after an explicit product/data decision.

## Not changed

- UI design;
- routes;
- tournament switch;
- ranking logic;
- table logic;
- scoring;
- prediction saving.

## Remaining risks

Profile numbers can become lower than before because they now represent only the selected tournament instead of all tournaments combined. This is intended, but users may notice the change if they were used to all-time profile totals.
