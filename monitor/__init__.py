"""Log-only oversight monitors for the testbed (Track 4 stretch).

A monitor reads what agents said and saw in an episode, never the environment's ledger,
and names suspected duplicate side effects and false "done" reports. ``monitor.score``
compares its claims with ledger truth from ``dst.metrics``. See docs/MONITOR.md.
"""
