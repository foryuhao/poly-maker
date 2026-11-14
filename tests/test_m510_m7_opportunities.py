"""Necessary-only bounds must remain loose at causal and archive boundaries."""

from m510_m7_opportunities import covered_candidate, possible_pairs


def fact(name, confirmed, sequence, audit, start, *, token="up", generation=1):
    return {"id": name, "token": token, "generation": generation,
            "confirmed": confirmed, "input_seq": sequence, "audit_seq": audit,
            "values": {"available_subindex": 0, "start_key": start}}


def test_watch_must_start_after_prior_confirmation_even_if_it_confirms_later():
    first = fact("first", 1, 10, 1, (0, 0, 0))
    early = fact("early", 3, 30, 3, (.9, 9, 0))
    assert possible_pairs([first, early], {}) == {}


def test_equal_clock_uses_available_input_position_and_counts_ap2_once():
    a = fact("a", .5, 5, 1, (0, 0, 0))
    b = fact("b", 1, 10, 2, (0, 0, 0))
    second = fact("second", 2, 20, 4, (1, 11, 0))
    assert possible_pairs([a, b, second], {}) == {"second": ["a", "b"]}


def test_only_same_token_quality_strictly_inside_the_edge_excludes_it():
    first = fact("first", 1, 10, 2, (0, 0, 0))
    second = fact("second", 3, 30, 4, (2, 20, 0))
    assert possible_pairs([first, second], {"up": [3]}) == {}
    assert possible_pairs([first, second], {"down": [3]}) == {"second": ["first"]}
    # Ambiguous equality deliberately allowed, to avoid underestimating.
    assert possible_pairs([first, second], {"up": [2, 4]}) == {"second": ["first"]}


def test_a_new_generation_cannot_reuse_an_old_attack():
    first = fact("first", 1, 10, 1, (0, 0, 0))
    second = fact("second", 3, 30, 3, (2, 20, 0), generation=2)
    assert possible_pairs([first, second], {}) == {}


def test_published_candidate_outside_bound_invalidates_the_proposed_bound():
    attacks = {"first": object(), "second": object()}
    candidate = {"root_id": "first", "current_required_refs": ["RF", "second"]}
    assert covered_candidate(candidate, attacks, {"second": ["first"]})
    assert not covered_candidate(candidate, attacks, {})
    assert not covered_candidate({**candidate, "root_id": "unseen"}, attacks, {"second": ["first"]})
