from datetime import datetime, timezone


WC2026_PLAYOFF_START = datetime(2026, 6, 28, tzinfo=timezone.utc)

PLAYOFF_STAGES = (
    ("round_32", "1/16 финала"),
    ("round_16", "1/8 финала"),
    ("quarter_final", "1/4 финала"),
    ("semi_final", "1/2 финала"),
    ("third_place", "Матч за 3-е место"),
    ("final", "Финал"),
)

PLAYOFF_STAGE_LABELS = dict(PLAYOFF_STAGES)
PLAYOFF_STAGE_ORDER = {key: index for index, (key, _label) in enumerate(PLAYOFF_STAGES, start=1)}


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


def normalize_playoff_stage(value):
    value = (value or "").strip()
    if not value:
        return None
    return value if value in PLAYOFF_STAGE_LABELS else None


def get_playoff_stage_label(value):
    return PLAYOFF_STAGE_LABELS.get(value)


def get_playoff_stage_sort_order(value):
    return PLAYOFF_STAGE_ORDER.get(value, 999)
