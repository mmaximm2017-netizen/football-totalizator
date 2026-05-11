# app/models/scoring.py

def calculate_points(real_home, real_away, pred_home, pred_away):
    if real_home is None or real_away is None:
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
        return 11 if abs(real_diff) >= 3 else 10
    if real_out == pred_out and real_diff == pred_diff:
        return 8 if big_margin else 7
    if real_out == pred_out and abs(real_diff - pred_diff) == 1:
        return 6 if big_margin else 5
    if real_out == pred_out and abs(real_diff) >= 3 and abs(pred_diff) >= 3:
        return 4
    if abs(real_diff - pred_diff) == 1:
        return 2
    if real_out == pred_out:
        return 3
    return 0