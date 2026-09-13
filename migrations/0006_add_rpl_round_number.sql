-- Add an explicit RPL round number so analytics never has to infer rounds
-- from dates, weeks, or "every 8 matches" after this one-time migration.
--
-- Fresh CI databases have no RPL rows when migrations run, so the backfill is
-- intentionally skipped there. Production backfill is fail-closed: the known
-- 2026/27 calendar must still be exactly 17 complete 8-match groups with all
-- 16 clubs represented once per group before any round numbers are written.

ALTER TABLE public.matches
ADD COLUMN IF NOT EXISTS round_number integer;

DO $$
DECLARE
    rpl_match_count integer;
    invalid_group_count integer;
BEGIN
    SELECT COUNT(*)
      INTO rpl_match_count
      FROM public.matches
     WHERE tournament_id = 5
       AND league = 'rpl'
       AND match_category = 'rpl';

    IF rpl_match_count = 0 THEN
        -- Fresh/test database. Nothing to backfill.
        RETURN;
    END IF;

    IF rpl_match_count <> 136 THEN
        RAISE EXCEPTION
            'RPL round backfill refused: expected 136 league matches, found %',
            rpl_match_count;
    END IF;

    WITH ranked AS (
        SELECT
            id,
            home_team,
            away_team,
            ((ROW_NUMBER() OVER (ORDER BY kickoff_time, id) - 1) / 8)::integer + 1
                AS candidate_round
        FROM public.matches
        WHERE tournament_id = 5
          AND league = 'rpl'
          AND match_category = 'rpl'
    ),
    grouped AS (
        SELECT
            candidate_round,
            COUNT(DISTINCT id) AS match_count,
            COUNT(DISTINCT team_name) AS team_count
        FROM ranked
        CROSS JOIN LATERAL unnest(ARRAY[home_team, away_team]) AS team_name
        GROUP BY candidate_round
    )
    SELECT COUNT(*)
      INTO invalid_group_count
      FROM grouped
     WHERE match_count <> 8
        OR team_count <> 16;

    IF invalid_group_count <> 0 THEN
        RAISE EXCEPTION
            'RPL round backfill refused: % candidate rounds failed 8-match/16-team validation',
            invalid_group_count;
    END IF;

    WITH ranked AS (
        SELECT
            id,
            ((ROW_NUMBER() OVER (ORDER BY kickoff_time, id) - 1) / 8)::integer + 1
                AS candidate_round
        FROM public.matches
        WHERE tournament_id = 5
          AND league = 'rpl'
          AND match_category = 'rpl'
    )
    UPDATE public.matches AS m
       SET round_number = ranked.candidate_round
      FROM ranked
     WHERE m.id = ranked.id
       AND m.round_number IS NULL;
END
$$;

-- CI/dev may not have the safe schema yet; production already does.
CREATE SCHEMA IF NOT EXISTS gpt_safe;
REVOKE ALL ON SCHEMA gpt_safe FROM PUBLIC;

CREATE OR REPLACE VIEW gpt_safe.matches AS
SELECT
    m.id AS match_id,
    m.tournament_id,
    t.name AS tournament_name,
    m.home_team,
    m.away_team,
    m.kickoff_time,
    m.deadline,
    m.status,
    CASE
        WHEN UPPER(COALESCE(m.status, ''))
             IN ('FINISHED', 'COMPLETE', 'COMPLETED')
        THEN m.home_score
        ELSE NULL
    END AS home_score,
    CASE
        WHEN UPPER(COALESCE(m.status, ''))
             IN ('FINISHED', 'COMPLETE', 'COMPLETED')
        THEN m.away_score
        ELSE NULL
    END AS away_score,
    m.playoff_stage AS stage,
    m.league,
    m.round_number
FROM public.matches AS m
LEFT JOIN public.tournaments AS t
    ON t.id = m.tournament_id;

CREATE OR REPLACE VIEW gpt_safe.predictions AS
SELECT
    p.match_id,
    p.user_id,
    p.tournament_id,
    u.username,
    t.name AS tournament_name,
    m.home_team,
    m.away_team,
    m.kickoff_time,
    m.deadline,
    m.status,
    p.home_goals AS predicted_home,
    p.away_goals AS predicted_away,
    CASE
        WHEN UPPER(COALESCE(m.status, ''))
             IN ('FINISHED', 'COMPLETE', 'COMPLETED')
        THEN m.home_score
        ELSE NULL
    END AS actual_home,
    CASE
        WHEN UPPER(COALESCE(m.status, ''))
             IN ('FINISHED', 'COMPLETE', 'COMPLETED')
        THEN m.away_score
        ELSE NULL
    END AS actual_away,
    CASE
        WHEN UPPER(COALESCE(m.status, ''))
             IN ('FINISHED', 'COMPLETE', 'COMPLETED')
        THEN p.points
        ELSE NULL
    END AS points,
    m.playoff_stage AS stage,
    m.league,
    m.round_number
FROM public.predictions AS p
JOIN public.matches AS m
    ON m.id = p.match_id
   AND m.tournament_id = p.tournament_id
JOIN public.users AS u
    ON u.id = p.user_id
LEFT JOIN public.tournaments AS t
    ON t.id = p.tournament_id
WHERE u.is_admin = 0
  AND COALESCE(u.is_deleted, 0) = 0
  AND m.deadline IS NOT NULL
  AND m.deadline <= CURRENT_TIMESTAMP;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'totish_gpt_reader') THEN
        GRANT USAGE ON SCHEMA gpt_safe TO totish_gpt_reader;
        GRANT SELECT ON gpt_safe.matches, gpt_safe.predictions TO totish_gpt_reader;
    END IF;
END
$$;
