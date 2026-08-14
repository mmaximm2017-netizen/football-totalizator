from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


MSK = ZoneInfo("Europe/Moscow")


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

    cur.execute(
        """
        INSERT INTO matches (
            api_match_id, home_team, away_team, kickoff_time, deadline,
            status, league, tournament_id, playoff_stage_manual, match_category
        )
        VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        ),
    )
    row = cur.fetchone()
    return row[0] if row else None
