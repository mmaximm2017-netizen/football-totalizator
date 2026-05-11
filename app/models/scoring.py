# app/models/scoring.py
import logging
logger = logging.getLogger(__name__)

def calculate_points(real_home, real_away, pred_home, pred_away):
    logger.info(f"calculate_points: real={real_home}:{real_away}, pred={pred_home}:{pred_away}")
    
    if real_home is None or real_away is None:
        logger.info("  -> 0 (None)")
        return 0
        
    real_diff = real_home - real_away
    pred_diff = pred_home - pred_away

    def outcome(diff):
        if diff > 0: return 1
        elif diff == 0: return 0
        else: return -1

    real_out = outcome(real_diff)
    pred_out = outcome(pred_diff)
    big_margin = abs(real_diff) >= 3

    if real_home == pred_home and real_away == pred_away:
        pts = 11 if abs(real_diff) >= 3 else 10
        logger.info(f"  -> {pts} (точный счёт)")
        return pts
    if real_out == pred_out and real_diff == pred_diff:
        pts = 8 if big_margin else 7
        logger.info(f"  -> {pts} (точная разница)")
        return pts
    if real_out == pred_out and abs(real_diff - pred_diff) == 1:
        pts = 6 if big_margin else 5
        logger.info(f"  -> {pts} (ошибка в разнице на 1)")
        return pts
    if real_out == pred_out and abs(real_diff) >= 3 and abs(pred_diff) >= 3:
        logger.info("  -> 4 (обе разницы >=3)")
        return 4
    if abs(real_diff - pred_diff) == 1:
        logger.info("  -> 2 (ошибка в разнице на 1)")
        return 2
    if real_out == pred_out:
        logger.info("  -> 3 (исход угадан)")
        return 3
    logger.info("  -> 0 (исход не угадан)")
    return 0