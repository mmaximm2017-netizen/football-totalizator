import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


MSK = ZoneInfo("Europe/Moscow")
ROUND_RE = re.compile(r"(?:тур|round)\s*(\d+)", re.IGNORECASE)


class ManualMatchValidationError(ValueError):
    pass


class DuplicateMatchError(ManualMatchValidationError):
    pass


@dataclass(frozen=True)
class ManualMatchCreateData:
    tournament_id: int
    league: str
    home_team: str
    away_team: str
    match_date: str
    match_time: str
    status: str = "SCHEDULED"
    stage: str = ""
    match_category: str = "rpl"
    deadline_date: str = ""
    deadline_time: str = ""
    reject_early_auto_deadline: bool = False
    round_number: int | None = None


def build_manual_deadline_utc(
    match_date,
    match_time,
    deadline_date="",
    deadline_time="",
    *,
    reject_early_auto=False,
):
    dt_msk = datetime.strptime(
        f"{match_date} {match_time}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=MSK)
    kickoff_utc = dt_msk.astimezone(timezone.utc)

    if deadline_date or deadline_time:
        if not deadline_date or not deadline_time:
            raise ManualMatchValidationError("Укажите обе дату и время дедлайна")
        deadline_msk = datetime.strptime(
            f"{deadline_date} {deadline_time}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=MSK)
    else:
        deadline_msk = dt_msk.replace(hour=11, minute=0, second=0, microsecond=0)
        if reject_early_auto and deadline_msk >= dt_msk:
            raise ManualMatchValidationError(
                "Матч начинается раньше стандартного дедлайна 11:00 "
                "(включая 11:00). Укажите дедлайн вручную."
            )

    return kickoff_utc, deadline_msk.astimezone(timezone.utc)


def normalize_round_number(value, stage=""):
    if value not in (None, ""):
        try:
            round_number = int(value)
        except (TypeError, ValueError) as exc:
            raise ManualMatchValidationError("Номер тура должен быть целым числом") from exc
        if round_number < 1:
            raise ManualMatchValidationError("Номер тура должен быть не меньше 1")
        return round_number

    match = ROUND_RE.search(str(stage or ""))
    if match:
        return int(match.group(1))
    return None


def infer_rpl_round_number(cur, tournament_id, kickoff_utc, home_team, away_team):
    """Infer only the next chronological RPL round, otherwise fail closed.

    Existing matches keep their stored round even when kickoff dates are moved.
    New imports normally arrive as an eight-match round in chronological order:
    the first match starts the next round and the remaining seven join it. If
    the latest round is incomplete but a new match is too far away, or either
    team is already present in that round, we refuse to guess.
    """
    cur.execute(
        """
        SELECT round_number,
               COUNT(*),
               MIN(kickoff_time),
               MAX(kickoff_time)
        FROM matches
        WHERE tournament_id = %s
          AND league = 'rpl'
          AND match_category = 'rpl'
          AND round_number IS NOT NULL
        GROUP BY round_number
        ORDER BY round_number DESC
        LIMIT 1
        """,
        (tournament_id,),
    )
    latest = cur.fetchone()
    if not latest:
        return 1

    latest_round, match_count, first_kickoff, last_kickoff = latest
    match_count = int(match_count or 0)

    if match_count > 8:
        raise ManualMatchValidationError(
            f"Тур {latest_round} содержит больше 8 матчей; номер нового тура нельзя определить автоматически"
        )

    if match_count == 8:
        if last_kickoff is not None and kickoff_utc <= last_kickoff:
            raise ManualMatchValidationError(
                "Матч добавляется не после последнего заполненного тура; укажите номер тура явно"
            )
        return int(latest_round) + 1

    cur.execute(
        """
        SELECT COUNT(*)
        FROM matches
        WHERE tournament_id = %s
          AND league = 'rpl'
          AND match_category = 'rpl'
          AND round_number = %s
          AND (home_team IN (%s, %s) OR away_team IN (%s, %s))
        """,
        (tournament_id, latest_round, home_team, away_team, home_team, away_team),
    )
    team_conflicts = int((cur.fetchone() or (0,))[0] or 0)
    if team_conflicts:
        raise ManualMatchValidationError(
            f"Команда уже есть в туре {latest_round}; номер тура нельзя определить автоматически"
        )

    if first_kickoff is not None:
        lower_bound = first_kickoff - timedelta(days=1)
        upper_bound = first_kickoff + timedelta(days=7)
        if not (lower_bound <= kickoff_utc <= upper_bound):
            raise ManualMatchValidationError(
                f"Тур {latest_round} ещё содержит только {match_count} матчей, "
                "а дата нового матча вне его 7-дневного окна; номер тура нельзя угадать"
            )

    return int(latest_round)


def create_manual_match(cur, data: ManualMatchCreateData):
    home_team = (data.home_team or "").strip()
    away_team = (data.away_team or "").strip()
    match_date = (data.match_date or "").strip()
    match_time = (data.match_time or "").strip()

    if not home_team or not away_team or not match_date or not match_time:
        raise ManualMatchValidationError("Заполните команды, дату и время")
    if home_team == away_team:
        raise ManualMatchValidationError("Команды должны отличаться")
    if not data.tournament_id:
        raise ManualMatchValidationError("Выберите турнир для матча")
    if data.status == "FINISHED":
        raise ManualMatchValidationError(
            "Для finished сначала создайте матч, затем внесите счёт"
        )

    kickoff_utc, deadline_utc = build_manual_deadline_utc(
        match_date,
        match_time,
        data.deadline_date,
        data.deadline_time,
        reject_early_auto=data.reject_early_auto_deadline,
    )

    cur.execute(
        """
        SELECT id
        FROM matches
        WHERE tournament_id = %s
          AND league = %s
          AND home_team = %s
          AND away_team = %s
          AND kickoff_time = %s
        """,
        (data.tournament_id, data.league, home_team, away_team, kickoff_utc),
    )
    if cur.fetchone():
        raise DuplicateMatchError("Такой матч уже существует")

    round_number = normalize_round_number(data.round_number, data.stage)
    if (
        round_number is None
        and data.league == "rpl"
        and data.match_category == "rpl"
    ):
        round_number = infer_rpl_round_number(
            cur,
            data.tournament_id,
            kickoff_utc,
            home_team,
            away_team,
        )

    cur.execute(
        """
        INSERT INTO matches (
            api_match_id, home_team, away_team, kickoff_time, deadline,
            status, league, tournament_id, playoff_stage_manual, match_category,
            round_number
        )
        VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            home_team,
            away_team,
            kickoff_utc,
            deadline_utc,
            data.status,
            data.league,
            data.tournament_id,
            data.stage,
            data.match_category,
            round_number,
        ),
    )
    row = cur.fetchone()
    return row[0] if row else None
