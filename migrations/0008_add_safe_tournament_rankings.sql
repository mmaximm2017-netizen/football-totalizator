-- Aggregate-only archive rankings; no hidden prediction rows or private user fields.
CREATE OR REPLACE VIEW gpt_safe.tournament_rankings AS
SELECT t.id AS tournament_id, u.id AS user_id, u.username,
       COALESCE(SUM(p.points), 0) AS points,
       COUNT(p.points) AS forecasts,
       COUNT(*) FILTER (WHERE p.points >= 10) AS exact,
       COUNT(*) FILTER (WHERE p.points BETWEEN 7 AND 8) AS diff,
       COUNT(*) FILTER (WHERE p.points = 3) AS outcome
FROM public.tournaments t CROSS JOIN public.users u
LEFT JOIN (
    SELECT p.user_id, p.tournament_id, p.points
    FROM public.predictions p
    JOIN public.matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
    WHERE UPPER(m.status) IN ('FINISHED', 'COMPLETE', 'COMPLETED')
      AND m.kickoff_time <= CURRENT_TIMESTAMP
      AND m.deadline IS NOT NULL AND m.deadline <= CURRENT_TIMESTAMP
) p ON p.user_id = u.id AND p.tournament_id = t.id
WHERE u.is_admin = 0 AND (t.is_active = 0 OR COALESCE(u.is_deleted, 0) = 0)
GROUP BY t.id, u.id, u.username;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'totish_gpt_reader') THEN
        GRANT SELECT ON gpt_safe.tournament_rankings TO totish_gpt_reader;
    END IF;
END
$$;
