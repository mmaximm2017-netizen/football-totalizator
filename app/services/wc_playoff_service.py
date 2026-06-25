from datetime import datetime, timezone


WC2026_PLAYOFF_START = datetime(2026, 6, 28, tzinfo=timezone.utc)


def is_wc2026_playoff_match(tournament_name, league, kickoff_time):
    if tournament_name is not None:
        if tournament_name != "ЧМ-2026":
            return False
    elif league != "wc2026":
        return False

    if not kickoff_time:
        return False

    if kickoff_time.tzinfo is None:
        kickoff_time = kickoff_time.replace(tzinfo=timezone.utc)

    return kickoff_time.astimezone(timezone.utc) >= WC2026_PLAYOFF_START
