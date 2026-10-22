-- Read-only calendar rounds for Telegram, independent of stored round_number.
-- One empty Moscow calendar day separates rounds, per tournament.
-- No result/prediction/points or stored round number is changed.
CREATE OR REPLACE VIEW gpt_safe.calendar_rounds AS
WITH included AS (
    SELECT m.id AS match_id, m.tournament_id,
           (m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date AS match_date
    FROM public.matches m
    WHERE m.kickoff_time IS NOT NULL
      AND m.league IN ('rpl', 'rcup')
      AND LOWER(COALESCE(m.match_category, '')) NOT IN ('national_team', 'supercup')
      AND COALESCE(m.home_team, '') !~* '(^|[^[:alpha:]])(россия|russia)([^[:alpha:]]|$)'
      AND COALESCE(m.away_team, '') !~* '(^|[^[:alpha:]])(россия|russia)([^[:alpha:]]|$)'
      AND COALESCE(m.playoff_stage, '') !~* '(суперкуб|super.?cup)'
), days AS (
    SELECT DISTINCT tournament_id, match_date FROM included
), islands AS (
    SELECT tournament_id, match_date,
           match_date - (ROW_NUMBER() OVER (PARTITION BY tournament_id ORDER BY match_date))::integer AS island
    FROM days
), numbered AS (
    SELECT tournament_id, match_date,
           DENSE_RANK() OVER (PARTITION BY tournament_id ORDER BY island)::integer AS round_number
    FROM islands
)
SELECT i.match_id, i.tournament_id, n.round_number, i.match_date
FROM included i
JOIN numbered n ON n.tournament_id = i.tournament_id AND n.match_date = i.match_date;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'totish_gpt_reader') THEN
        GRANT SELECT ON gpt_safe.calendar_rounds TO totish_gpt_reader;
    END IF;
END
$$;
