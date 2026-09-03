ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS result_origin TEXT;

CREATE OR REPLACE FUNCTION clear_auto_result_origin_after_manual_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.result_origin = 'auto_result_worker'
       AND NEW.result_origin = 'auto_result_worker'
       AND (
            OLD.home_score IS DISTINCT FROM NEW.home_score
            OR OLD.away_score IS DISTINCT FROM NEW.away_score
            OR OLD.status IS DISTINCT FROM NEW.status
       ) THEN
        NEW.result_origin := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_clear_auto_result_origin_after_manual_change ON matches;
CREATE TRIGGER trg_clear_auto_result_origin_after_manual_change
BEFORE UPDATE ON matches
FOR EACH ROW
EXECUTE FUNCTION clear_auto_result_origin_after_manual_change();
