def apply_home_match_card_state(match):
    """Attach the canonical visual state used by the home match card root."""
    finished = bool(match.get("finished"))
    deadline_closed = bool(match.get("deadline_passed"))
    has_prediction = match.get("pred_home") != ""

    if finished:
        card_state = "finished"
    elif deadline_closed:
        card_state = "closed"
    else:
        card_state = "active"

    match["has_prediction"] = has_prediction
    match["card_state"] = card_state
    match["predicted_class"] = (
        "predicted"
        if has_prediction and not finished and not deadline_closed
        else ""
    )
    match["data_finished"] = "1" if finished else "0"
    match["data_deadline_closed"] = "1" if deadline_closed else "0"
    match["prediction_editable"] = not finished and not deadline_closed
    return match
