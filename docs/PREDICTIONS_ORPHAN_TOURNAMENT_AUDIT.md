# Predictions Orphan Tournament Audit

Read-only SQL for predictions that reference missing tournaments.

Do not run `UPDATE` or `DELETE` from this audit.

```sql
SELECT p.*
FROM predictions p
LEFT JOIN tournaments t ON t.id = p.tournament_id
WHERE t.id IS NULL;

SELECT p.tournament_id, COUNT(*)
FROM predictions p
LEFT JOIN tournaments t ON t.id = p.tournament_id
WHERE t.id IS NULL
GROUP BY p.tournament_id;
```
