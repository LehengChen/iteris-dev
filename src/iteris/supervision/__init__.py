"""Supervision engine: observe -> detect -> judge -> act -> record.

One small engine carries the invariant loop; declarative profiles carry
everything that varies (sensors, triggers, judgment contracts, actuators).
Durable state lives in files only — the engine is stateless across restarts.
"""
