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
    except:
        return 0


# =========================================================
# MAIN LOGIC
# =========================================================

def calculate_points(real_home, real_away, pred_home, pred_away):

    real_home = safe_int(real_home)
    real_away = safe_int(real_away)
    pred_home = safe_int(pred_home)
    pred_away = safe_int(pred_away)

    logger.info(
        f"calc_points: real={real_home}:{real_away}, pred={pred_home}:{pred_away}"
    )

    # если матч невалидный
    if real_home is None or real_away is None:
        return 0

    real_diff = real_home - real_away
    pred_diff = pred_home - pred_away

    def outcome(diff):
        if diff > 0:
            return 1
        elif diff == 0:
            return 0
        return -1

    real_out = outcome(real_diff)
    pred_out = outcome(pred_diff)

    big_margin = abs(real_diff) >= 3

    # =====================================================
    # 1. ТОЧНЫЙ СЧЁТ
    # =====================================================
    if real_home == pred_home and real_away == pred_away:
        pts = 11 if big_margin else 10
        logger.info(f" -> {pts} exact score")
        return pts

    # =====================================================
    # 2. ТОЧНАЯ РАЗНИЦА
    # =====================================================
    if real_out == pred_out and real_diff == pred_diff:
        pts = 8 if big_margin else 7
        logger.info(f" -> {pts} exact diff")
        return pts

    # =====================================================
    # 3. ОШИБКА В 1 ГОЛ ПО РАЗНИЦЕ
    # =====================================================
    if real_out == pred_out and abs(real_diff - pred_diff) == 1:
        pts = 6 if big_margin else 5
        logger.info(f" -> {pts} diff off by 1")
        return pts

    # =====================================================
    # 4. ОБА ВЫИГРЫШ/НИЧЬЯ/ПРОИГРЫШ УГАДАНЫ
    # =====================================================
    if real_out == pred_out and abs(real_diff) >= 3 and abs(pred_diff) >= 3:
        logger.info(" -> 4 same outcome big margin")
        return 4

    # =====================================================
    # 5. ОШИБКА В 1 ГОЛ (ГРУБАЯ)
    # =====================================================
    if abs(real_diff - pred_diff) == 1:
        logger.info(" -> 2 diff off by 1 (fallback)")
        return 2

    # =====================================================
    # 6. ТОЛЬКО ИСХОД
    # =====================================================
    if real_out == pred_out:
        logger.info(" -> 3 correct outcome")
        return 3

    # =====================================================
    # 7. НИЧЕГО НЕ УГАДАНО
    # =====================================================
    logger.info(" -> 0 no match")
    return 0