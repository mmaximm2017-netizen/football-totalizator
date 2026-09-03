-- Canonical core-schema bootstrap for fresh databases.
-- Existing production installations already have these tables; CREATE IF NOT EXISTS
-- makes this migration a no-op there while allowing empty CI/dev databases to be
-- created exclusively through the versioned migration path.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    is_deleted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tournaments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    start_date TEXT,
    end_date TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    api_match_id TEXT UNIQUE,
    home_team TEXT,
    away_team TEXT,
    kickoff_time TIMESTAMP WITH TIME ZONE,
    deadline TIMESTAMP WITH TIME ZONE,
    status TEXT DEFAULT 'SCHEDULED',
    home_score INTEGER,
    away_score INTEGER,
    manual_teams_override INTEGER DEFAULT 0,
    manual_result_override INTEGER DEFAULT 0,
    manual_kickoff_override INTEGER DEFAULT 0,
    playoff_stage TEXT,
    playoff_stage_manual TEXT,
    playoff_stage_auto TEXT,
    match_category VARCHAR(32) DEFAULT 'rpl',
    api_conflict_note TEXT,
    league TEXT DEFAULT 'other',
    tournament_id INTEGER REFERENCES tournaments(id)
);

CREATE TABLE IF NOT EXISTS predictions (
    user_id INTEGER NOT NULL,
    match_id INTEGER NOT NULL,
    tournament_id INTEGER NOT NULL DEFAULT 1,
    home_goals INTEGER,
    away_goals INTEGER,
    points INTEGER DEFAULT 0,
    CONSTRAINT pred_unique UNIQUE (user_id, match_id, tournament_id),
    CONSTRAINT predictions_user_fk FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT predictions_match_fk FOREIGN KEY (match_id) REFERENCES matches(id),
    CONSTRAINT predictions_tournament_fk FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
);

CREATE TABLE IF NOT EXISTS user_titles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    awarded_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    awarded_by INTEGER NULL,
    UNIQUE (user_id, title)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    finished_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    status TEXT NOT NULL,
    matches_inserted INTEGER DEFAULT 0,
    matches_updated INTEGER DEFAULT 0,
    matches_finished INTEGER DEFAULT 0,
    predictions_recalculated INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    summary_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_time);
CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league);
CREATE INDEX IF NOT EXISTS idx_matches_tournament ON matches(tournament_id);
CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id);
CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);
CREATE INDEX IF NOT EXISTS idx_predictions_tournament ON predictions(tournament_id);
CREATE INDEX IF NOT EXISTS idx_predictions_match_tournament ON predictions(match_id, tournament_id);
CREATE INDEX IF NOT EXISTS idx_user_titles_user ON user_titles(user_id);
CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_sync_runs_status ON sync_runs(status);

-- The old deployment model allowed this unique index to exist. Multi-tournament
-- operation requires more than one active tournament, so keep the already-adopted
-- cleanup in the versioned schema path.
DROP INDEX IF EXISTS idx_tournaments_single_active;
