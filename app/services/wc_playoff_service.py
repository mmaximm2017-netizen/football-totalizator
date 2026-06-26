from datetime import datetime, timezone


# The group stage can include late June 27 local-time matches that are already
# June 28 in UTC. Keep the fallback cutoff safely after those fixtures so group
# matches never receive the generic playoff design just because stage is unknown.
WC2026_PLAYOFF_START = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)

PLAYOFF_STAGES = (
    ("playoff", "Плей-офф"),
    ("round_32", "1/16 финала"),
    ("round_16", "1/8 финала"),
    ("quarter_final", "1/4 финала"),
    ("semi_final", "1/2 финала"),
    ("third_place", "Матч за 3-е место"),
    ("final", "Финал"),
)

PLAYOFF_STAGE_LABELS = dict(PLAYOFF_STAGES)
PLAYOFF_STAGE_ORDER = {key: index for index, (key, _label) in enumerate(PLAYOFF_STAGES, start=1)}

PLAYOFF_STAGE_ALIASES = {
    "playoff": "playoff",
    "knockout": "playoff",
    "round_32": "round_32",
    "round-of-32": "round_32",
    "round of 32": "round_32",
    "last_32": "round_32",
    "r32": "round_32",
    "1/16": "round_32",
    "1/16 финала": "round_32",
    "round_16": "round_16",
    "round-of-16": "round_16",
    "round of 16": "round_16",
    "last_16": "round_16",
    "r16": "round_16",
    "1/8": "round_16",
    "1/8 финала": "round_16",
    "quarter_final": "quarter_final",
    "quarter-final": "quarter_final",
    "quarter final": "quarter_final",
    "quarterfinal": "quarter_final",
    "qf": "quarter_final",
    "1/4": "quarter_final",
    "1/4 финала": "quarter_final",
    "semi_final": "semi_final",
    "semi-final": "semi_final",
    "semi final": "semi_final",
    "semifinal": "semi_final",
    "sf": "semi_final",
    "1/2": "semi_final",
    "1/2 финала": "semi_final",
    "third_place": "third_place",
    "third-place": "third_place",
    "third place": "third_place",
    "bronze": "third_place",
    "3rd": "third_place",
    "final": "final",
    "f": "final",
}


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
    value = (value or "").strip().lower()
    if not value:
        return None
    return PLAYOFF_STAGE_ALIASES.get(value)


def infer_playoff_stage_from_api(match):
    if not match:
        return None

    candidates = [
        match.get("stage"),
        match.get("round"),
        match.get("roundName"),
        match.get("round_name"),
        match.get("group"),
    ]
    for candidate in candidates:
        stage = normalize_playoff_stage(candidate)
        if stage:
            return stage

    for candidate in (match.get("matchday"), match.get("round_number")):
        try:
            number = int(candidate)
        except (TypeError, ValueError):
            continue
        if 73 <= number <= 88:
            return "round_32"
        if 89 <= number <= 96:
            return "round_16"
        if 97 <= number <= 100:
            return "quarter_final"
        if 101 <= number <= 102:
            return "semi_final"
        if number == 103:
            return "third_place"
        if number == 104:
            return "final"

    return None


def determine_effective_playoff_stage(manual_stage=None, auto_stage=None, match=None):
    return (
        normalize_playoff_stage(manual_stage)
        or normalize_playoff_stage(auto_stage)
        or infer_playoff_stage_from_api(match)
        or "playoff"
    )


def get_playoff_stage_label(value):
    return PLAYOFF_STAGE_LABELS.get(value)


def get_playoff_stage_sort_order(value):
    return PLAYOFF_STAGE_ORDER.get(value, 999)
