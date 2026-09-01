# Performance Index Candidates

No index in this document has been created. Every candidate requires production
`EXPLAIN (ANALYZE, BUFFERS)` and index-catalog inspection before approval.

## Matches by tournament and kickoff

```sql
SELECT ...
FROM matches m
WHERE m.tournament_id = $1
  AND m.kickoff_time >= $2
ORDER BY m.kickoff_time;
```

Candidate: `(tournament_id, kickoff_time)`.

Existing source-defined indexes are single-column `matches(tournament_id)` and
`matches(kickoff_time)`. This composite index may help the home page and agent
match listings only if EXPLAIN shows filtering and ordering dominate. Write cost:
additional index maintenance for every match sync/update.

## Matches by tournament, league, and kickoff

```sql
SELECT ...
FROM matches m
WHERE m.tournament_id = $1
  AND m.league = $2
ORDER BY m.kickoff_time;
```

Candidate: `(tournament_id, league, kickoff_time)`.

This is a hypothesis for tournament-specific league pages. It should be rejected
if `league` is not selective or the shorter tournament/kickoff index covers the
measured plan sufficiently. Write cost: another index on match imports.

## Prediction history and profile aggregates

```sql
SELECT ...
FROM predictions p
JOIN matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
WHERE p.user_id = $1
  AND p.tournament_id = $2;
```

Candidate: `(user_id, tournament_id)` on `predictions`.

The existing unique key begins `(user_id, match_id, tournament_id)`, which does
not directly cover a user-plus-tournament history scan. Confirm with EXPLAIN on
real profile/history query volume before adding it. Write cost: every prediction
insert/update maintains another index.

## Explicit non-candidates until measured

- Functional status/date indexes for `UPPER(status)` or Moscow date expressions.
- Trigram indexes for `ILIKE '%team%'` admin/GPT search.
- Tournament `name`, `is_active`, or `start_date` indexes.

These queries need production plans and table cardinality first; a sequential scan
on the current small dataset may be cheaper than index overhead.
