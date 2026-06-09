import logging

logger = logging.getLogger(__name__)


# =========================================================
# SAFE INT
# =========================================================

def safe_int(value):
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


# =========================================================
# MAIN LOGIC
# =========================================================

def calculate_points(real_home, real_away, pred_home, pred_away):
    real_home = safe_int(real_home)
    real_away = safe_int(real_away)
    pred_home = safe_int(pred_home)
    pred_away = safe_int(pred_away)

    logger.debug(
        f"calc_points: real={real_home}:{real_away}, pred={pred_home}:{pred_away}"
    )

    real_diff = real_home - real_away
    pred_diff = pred_home - pred_away

    def outcome(diff):
        if diff > 0:
            return 1
        if diff == 0:
            return 0
        return -1

    real_out = outcome(real_diff)
    pred_out = outcome(pred_diff)

    big_margin = abs(real_diff) >= 3

    # 1. Точный счёт: бонус +1 только при фактической разнице 3+.
    if real_home == pred_home and real_away == pred_away:
        pts = 11 if big_margin else 10
        logger.debug(f" -> {pts} exact score")
        return pts

    # 2. Точная разница: бонус +1 только при фактической разнице 3+.
    if real_out == pred_out and real_diff == pred_diff:
        pts = 8 if big_margin else 7
        logger.debug(f" -> {pts} exact diff")
        return pts

    # 3. Ошибка в разнице на 1 гол. Бонус за крупный счёт не применяется.
    if real_out == pred_out and abs(real_diff - pred_diff) == 1:
        logger.debug(" -> 5 diff off by 1")
        return 5

    # 4. Только исход. Бонус за крупный счёт не применяется.
    if real_out == pred_out:
        logger.debug(" -> 3 correct outcome")
        return 3

    # 5. Ничья <-> победа/поражение в 1 гол.
    if (
        (real_out != 0 and abs(real_diff) == 1 and pred_out == 0)
        or (real_out == 0 and pred_out != 0 and abs(pred_diff) == 1)
    ):
        logger.debug(" -> 2 draw and one-goal result crossover")
        return 2

    # 6. Ничего не угадано.
    logger.debug(" -> 0 no match")
    return 0
