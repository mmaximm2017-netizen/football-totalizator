# Prediction Integrity Diagnostics

Run these read-only PostgreSQL queries manually before a production integrity migration.

```sql
SELECT p.*
FROM predictions p
LEFT JOIN users u ON u.id = p.user_id
WHERE u.id IS NULL;

SELECT p.*
FROM predictions p
LEFT JOIN matches m ON m.id = p.match_id
WHERE m.id IS NULL;

SELECT p.*
FROM predictions p
LEFT JOIN tournaments t ON t.id = p.tournament_id
WHERE t.id IS NULL;

SELECT p.user_id, p.match_id, p.tournament_id AS prediction_tournament_id,
       m.tournament_id AS match_tournament_id, p.home_goals, p.away_goals, p.points
FROM predictions p
JOIN matches m ON m.id = p.match_id
WHERE p.tournament_id IS DISTINCT FROM m.tournament_id;

SELECT user_id, match_id, tournament_id, COUNT(*)
FROM predictions
GROUP BY user_id, match_id, tournament_id
HAVING COUNT(*) > 1;

SELECT user_id, match_id, COUNT(*)
FROM predictions
GROUP BY user_id, match_id
HAVING COUNT(*) > 1;

SELECT COUNT(*) AS prediction_count,
       COALESCE(SUM(points), 0) AS points_sum,
       COUNT(DISTINCT user_id) AS users_count,
       COUNT(DISTINCT match_id) AS matches_count,
       COUNT(DISTINCT tournament_id) AS tournaments_count,
       COUNT(*) FILTER (WHERE user_id IS NULL OR match_id IS NULL OR tournament_id IS NULL) AS null_key_count
FROM predictions;

SELECT tournament_id, COUNT(*) AS prediction_count, COALESCE(SUM(points), 0) AS points_sum
FROM predictions
GROUP BY tournament_id
ORDER BY tournament_id;

SELECT user_id, COUNT(*) AS prediction_count, COALESCE(SUM(points), 0) AS points_sum
FROM predictions
GROUP BY user_id
ORDER BY user_id;
```
