"""Offline production coordinator exercise with the main mode and compressed audit."""
from m234_sim_main import experiment_definition
from m234_sim_main_storage import ForwardEngine, storage_definition

import m1234_live_driver as driver

original = driver.rr1_events


def events(mode):
    tape = original(mode)
    tape[0]["definition"]["settings"].update(
        m234_execution_main=experiment_definition(), audit_storage=storage_definition())
    return tape


driver.rr1_events = events
driver.runner.Engine = ForwardEngine
driver.main()
