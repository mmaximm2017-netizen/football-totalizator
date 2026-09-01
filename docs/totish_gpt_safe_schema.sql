-- Safe analytics schema for TOTISH Custom GPT.
-- Run manually as the database owner.
--
-- IMPORTANT:
-- This stage creates the isolated safe views and grants access to them.
-- It DOES NOT revoke the reader's existing SELECT privileges on public.* yet.
-- Revoke is a separate final migration after production verification.

BEGIN;

CREATE SCHEMA IF NOT EXISTS gpt_safe;

REVOKE ALL ON SCHEMA gpt_safe FROM PUBLIC;


DROP VIEW IF EXISTS gpt_safe.predictions;
DROP VIEW IF EXISTS gpt_safe.matches;
DROP VIEW IF EXISTS gpt_safe.users;
DROP VIEW IF EXISTS gpt_safe.tournaments;


-- ============================================================
-- USERS
-- Only public participant identity needed for analytics.
-- No passwords, hashes, tokens, emails or service fields.
-- ============================================================

CREATE VIEW gpt_safe.users AS
SELECT
    u.id AS user_id,
    u.username
FROM public.users u
WHERE u.is_admin = 0
  AND COALESCE(u.is_deleted, 0) = 0;


-- ============================================================
-- TOURNAMENTS
-- ============================================================

CREATE VIEW gpt_safe.tournaments AS
SELECT
    t.id AS tournament_id,
    t.name AS tournament_name,
    t.is_active,
    t.start_date,
    t.end_date
FROM public.tournaments t;


-- ============================================================
-- MATCHES
--
-- Match metadata is visible.
-- Final scores are exposed only for finished matches.
-- ============================================================

CREATE VIEW gpt_safe.matches AS
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
    m.league

FROM public.matches m

LEFT JOIN public.tournaments t
    ON t.id = m.tournament_id;


-- ============================================================
-- PREDICTIONS
--
-- SECURITY RULE:
-- A prediction row does not exist in this view until the match
-- deadline has passed.
--
-- This means a GPT query cannot discover future predictions
-- even by filtering, counting rows, checking NULLs, grouping,
-- joining, or using aggregates.
--
-- Actual result and points are additionally exposed only after
-- the match is finished.
-- ============================================================

CREATE VIEW gpt_safe.predictions AS
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
    m.league

FROM public.predictions p

JOIN public.matches m
    ON m.id = p.match_id
   AND m.tournament_id = p.tournament_id

JOIN public.users u
    ON u.id = p.user_id

LEFT JOIN public.tournaments t
    ON t.id = p.tournament_id

WHERE
    u.is_admin = 0
    AND COALESCE(u.is_deleted, 0) = 0

    -- Hard privacy boundary for future predictions.
    AND m.deadline IS NOT NULL
    AND m.deadline <= CURRENT_TIMESTAMP;


-- ============================================================
-- READER ACCESS
--
-- At this stage we grant the safe schema.
-- Direct public.* access will be revoked separately only after
-- the new production API is deployed and verified.
-- ============================================================

GRANT USAGE ON SCHEMA gpt_safe TO totish_gpt_reader;

GRANT SELECT ON
    gpt_safe.users,
    gpt_safe.tournaments,
    gpt_safe.matches,
    gpt_safe.predictions
TO totish_gpt_reader;

COMMIT;
