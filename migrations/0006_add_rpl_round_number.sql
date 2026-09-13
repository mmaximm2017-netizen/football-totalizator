-- Add an explicit RPL round number so analytics never has to infer rounds
-- from dates, weeks, or "every 8 matches" after this one-time migration.
--
-- Fresh CI databases have no RPL rows when migrations run, so the backfill is
-- intentionally skipped there. Production backfill is fail-closed: the known
-- 2026/27 calendar must still be exactly 17 complete 8-match groups with all
-- 16 clubs represented once per group before any round numbers are written.
--
-- Core-table references intentionally follow search_path. Integration tests
-- apply migrations inside isolated schemas; production uses public as its
-- application schema. The gpt_safe views below remain explicitly bound to
-- public production tables.

ALTER TABLE matches
ADD COLUMN IF NOT EXISTS round_number integer;

DO $$
DECLARE
    rpl_match_count integer;
    invalid_group_count integer;
BEGIN
    SELECT COUNT(*)
      INTO rpl_match_count
      FROM matches
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
        FROM matches
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
        FROM matches
        WHERE tournament_id = 5
          AND league = 'rpl'
          AND match_category = 'rpl'
    )
    UPDATE matches AS m
       SET round_number = ranked.candidate_round
      FROM ranked
     WHERE m.id = ranked.id
       AND m.round_number IS NULL;
END
$$;

-- All future insert paths (manual admin, OCR import and agent API) pass through
-- the same fail-closed assignment rule. Explicit round_number wins. A manual
-- stage like "Тур 18" is also treated as explicit. Otherwise the first match
-- after a complete 8-match round starts the next round and subsequent matches
-- may join it only within a seven-day window and only if neither club is
-- already present. Suspicious/out-of-order inserts are rejected instead of
-- silently guessing the round.
CREATE OR REPLACE FUNCTION public.assign_rpl_round_number()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    stage_match text[];
    latest_round integer;
    latest_count integer;
    latest_first timestamptz;
    latest_last timestamptz;
    team_conflicts integer;
BEGIN
    IF NEW.round_number IS NOT NULL
       OR NEW.league IS DISTINCT FROM 'rpl'
       OR COALESCE(NEW.match_category, 'rpl') <> 'rpl'
    THEN
        RETURN NEW;
    END IF;

    stage_match := regexp_match(
        COALESCE(NEW.playoff_stage_manual, ''),
        '(тур|round)[[:space:]]*([0-9]+)',
        'i'
    );
    IF stage_match IS NOT NULL THEN
        NEW.round_number := stage_match[2]::integer;
        IF NEW.round_number < 1 THEN
            RAISE EXCEPTION 'RPL round number must be >= 1';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.kickoff_time IS NULL THEN
        RAISE EXCEPTION 'RPL round number cannot be inferred without kickoff_time';
    END IF;

    SELECT round_number,
           COUNT(*),
           MIN(kickoff_time),
           MAX(kickoff_time)
      INTO latest_round, latest_count, latest_first, latest_last
      FROM matches
     WHERE tournament_id = NEW.tournament_id
       AND league = 'rpl'
       AND match_category = 'rpl'
       AND round_number IS NOT NULL
     GROUP BY round_number
     ORDER BY round_number DESC
     LIMIT 1;

    IF latest_round IS NULL THEN
        NEW.round_number := 1;
        RETURN NEW;
    END IF;

    IF latest_count > 8 THEN
        RAISE EXCEPTION
            'RPL round % already contains % matches; refusing automatic round assignment',
            latest_round, latest_count;
    END IF;

    IF latest_count = 8 THEN
        IF latest_last IS NOT NULL AND NEW.kickoff_time <= latest_last THEN
            RAISE EXCEPTION
                'RPL match is not after latest complete round %; supply round_number explicitly',
                latest_round;
        END IF;
        NEW.round_number := latest_round + 1;
        RETURN NEW;
    END IF;

    SELECT COUNT(*)
      INTO team_conflicts
      FROM matches
     WHERE tournament_id = NEW.tournament_id
       AND league = 'rpl'
       AND match_category = 'rpl'
       AND round_number = latest_round
       AND (
            home_team IN (NEW.home_team, NEW.away_team)
         OR away_team IN (NEW.home_team, NEW.away_team)
       );

    IF team_conflicts > 0 THEN
        RAISE EXCEPTION
            'RPL team already exists in incomplete round %; supply round_number explicitly',
            latest_round;
    END IF;

    IF latest_first IS NOT NULL
       AND (
            NEW.kickoff_time < latest_first - INTERVAL '1 day'
         OR NEW.kickoff_time > latest_first + INTERVAL '7 days'
       )
    THEN
        RAISE EXCEPTION
            'RPL round % is incomplete and new kickoff is outside its 7-day window; supply round_number explicitly',
            latest_round;
    END IF;

    NEW.round_number := latest_round;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_assign_rpl_round_number
BEFORE INSERT ON matches
FOR EACH ROW
EXECUTE FUNCTION public.assign_rpl_round_number();

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
