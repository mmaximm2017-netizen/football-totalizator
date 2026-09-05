"""Pure quorum and timing policy shared by discovery and DB finalization."""
from collections import Counter

FIRST_CHECK_MINUTES = 120
SOFT_DEADLINE_MINUTES = 180
HARD_DEADLINE_MINUTES = 360


def quorum(observations):
    # One adapter is one vote, regardless of how many rows it returned.
    sources = [o.source for o in observations]
    if len(sources) != len(set(sources)):
        return {'decision': 'waiting', 'error': 'duplicate_source_identity'}
    votes = {}
    for o in observations:
        if o.status == 'finished':
            score = (o.home_score, o.away_score)
            if not all(type(v) is int and 0 <= v <= 99 for v in score):
                return {'decision': 'waiting', 'error': 'invalid_finished_score'}
            votes[o.source] = score
    counts = Counter(votes.values())
    conflict = len(counts) > 1
    winner = next((score for score, count in counts.items() if count >= 2), None)
    if winner is not None:
        result = {'decision': 'would_write', 'score': winner}
        if conflict:
            result.update(conflict=True, votes=votes)
        return result
    if conflict:
        scores = list(counts)
        return {'decision': 'score_conflict', 'first_score': scores[0],
                'second_score': scores[1], 'votes': votes}
    if len(votes) == 1:
        source, score = next(iter(votes.items()))
        return {'decision': 'one_source_confirmed', 'confirmed_source': source, 'score': score}
    return {'decision': 'waiting'}
