from maker_signals.primitives import Rounds
from maker_signals.strategies import M5_M10_NEXT, NEXT_PARAMETERS, Strategies

from test_m511_feed import Tape


def test_local_recovery_parameters_do_not_weaken_shared_refill():
    tape = Tape()
    default, changed = [], []
    for threshold, output in ((".50", default), (".40", changed)):
        tape.feed.observers.append(Rounds(tape.feed, "up", threshold, output.append,
                                          stable_recovery=threshold, hold_seconds=.05))
    tape.books(0)
    tape.trade(.01, "SELL", 10)
    tape.books(.02, bid=90)
    for at in (.10, .15, .20, .25, .30, .40, .50):
        tape.books(at, bid=94)
        tape.tick(at)
    tape.tick(.51)
    assert len(default) == len(changed) == 1
    assert not default[0].values["stable"] and changed[0].values["stable"]
    shared = [f for f in tape.feed.registry.facts.values()
              if f.kind == "ROUND" and f.values["scope"] == "shared-rf" and f.token == "up"]
    assert len(shared) == 1 and not shared[0].values["stable"]


def test_parameter_profiles_do_not_mutate_each_other_or_global_defaults():
    tape = Tape()
    a = Strategies(tape.feed, signal_variant=M5_M10_NEXT,
                   frequency_parameters={"M7": {"stable_recovery": ".55"}})
    b = Strategies(tape.feed, signal_variant=M5_M10_NEXT)
    assert a.frequency_parameters["M7"]["stable_recovery"] == ".55"
    assert b.frequency_parameters["M7"]["stable_recovery"] == NEXT_PARAMETERS["M7"]["stable_recovery"] == ".40"


def test_grid_primary_preserves_standalone_causal_candidates_and_quality():
    from m510_frequency_grid import attach_profiles

    from test_m511_feed import play_m10_book_path

    standalone, shared = Tape(), Tape()
    primary = Strategies(shared.feed, signal_variant=M5_M10_NEXT, parent_filters=False)
    attach_profiles(shared.feed, primary)
    Strategies(standalone.feed, signal_variant=M5_M10_NEXT, parent_filters=False)
    for tape in (standalone, shared):
        play_m10_book_path(tape)
    def signatures(tape, ids):
        return sorted((f.values["strategy"], f.token, f.confirmed, f.deadline, f.anchor)
                      for i in ids if (f := tape.feed.registry.facts[i]))
    assert signatures(shared, primary.published) == signatures(
        standalone, standalone.feed.strategies.published)
    assert shared.feed.good == standalone.feed.good
    assert shared.feed.reasons == standalone.feed.reasons
