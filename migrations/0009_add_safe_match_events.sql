-- Public match classification only; no scoring, fixtures or rounds are modified.
CREATE OR REPLACE VIEW gpt_safe.match_events AS
SELECT m.id AS match_id, m.tournament_id,
       CASE
           WHEN LOWER(COALESCE(m.match_category, '')) = 'supercup' THEN 'supercup'
           WHEN LOWER(COALESCE(m.match_category, '')) = 'national_team' THEN 'national'
           WHEN COALESCE(m.home_team, '') ~* '(^|[^[:alpha:]])(россия|russia)([^[:alpha:]]|$)'
             OR COALESCE(m.away_team, '') ~* '(^|[^[:alpha:]])(россия|russia)([^[:alpha:]]|$)' THEN 'national'
           WHEN COALESCE(m.playoff_stage, '') ~* '(суперкуб|super.?cup)' THEN 'supercup'
       END AS event_kind
FROM public.matches m
WHERE m.league = 'rpl' AND m.kickoff_time IS NOT NULL
  AND (LOWER(COALESCE(m.match_category, '')) IN ('national_team', 'supercup')
       OR COALESCE(m.home_team, '') ~* '(^|[^[:alpha:]])(россия|russia)([^[:alpha:]]|$)'
       OR COALESCE(m.away_team, '') ~* '(^|[^[:alpha:]])(россия|russia)([^[:alpha:]]|$)'
       OR COALESCE(m.playoff_stage, '') ~* '(суперкуб|super.?cup)');

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'totish_gpt_reader') THEN
        GRANT SELECT ON gpt_safe.match_events TO totish_gpt_reader;
    END IF;
END
$$;
