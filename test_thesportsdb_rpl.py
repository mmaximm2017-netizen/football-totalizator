import requests
from datetime import datetime

LEAGUE_ID = "4355"        # Russian Premier League
SEASON = "2024-2025"
URL = f"https://www.thesportsdb.com/api/v1/json/123/eventsseason.php?id={LEAGUE_ID}&s={SEASON}"


TEAM_MAP = {
    "Zenit St. Petersburg": "Зенит",
    "FC Rostov": "Ростов",
    "CSKA Moscow": "ЦСКА",
    "Lokomotiv Moscow": "Локомотив",
    "Dinamo Moscow": "Динамо Москва",
    "Dynamo Moscow": "Динамо Москва",
    "Krylya Sovetov": "Крылья Советов",
    "Akron Togliatti": "Акрон",
    "Baltika Kaliningrad": "Балтика",
}


def ru_team(name: str) -> str:
    return TEAM_MAP.get(name, name)


def main():
    print("Запрашиваю матчи РПЛ из TheSportsDB...")
    print(URL)

    response = requests.get(URL, timeout=20)
    response.raise_for_status()

    data = response.json()
    events = data.get("events") or []

    if not events:
        print("Матчи не найдены.")
        return

    print(f"\nНайдено матчей: {len(events)}\n")

    for event in events:
        date = event.get("dateEvent") or ""
        time = event.get("strTime") or ""
        home = ru_team(event.get("strHomeTeam") or "")
        away = ru_team(event.get("strAwayTeam") or "")
        home_score = event.get("intHomeScore")
        away_score = event.get("intAwayScore")
        status = event.get("strStatus") or ""

        score = ""
        if home_score is not None and away_score is not None:
            score = f" | счёт: {home_score}:{away_score}"

        print(f"{date} {time} | {home} — {away}{score} | status={status}")


if __name__ == "__main__":
    main()