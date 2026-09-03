-- Shared login throttling for multiple Gunicorn workers.
CREATE TABLE IF NOT EXISTS login_rate_limits (
    key_hash CHAR(64) PRIMARY KEY,
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    window_started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    blocked_until TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_login_rate_limits_blocked_until
    ON login_rate_limits (blocked_until);
