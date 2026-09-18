"""HoloQA — a local MCP referee for evidence-backed release checklists.

HoloQA holds no intelligence. It runs subprocesses, hashes files, matches JSON,
and computes verdicts by comparing captures against a human-written plan.
The connected AI client supplies the reasoning; HoloQA supplies the proof.
"""

__version__ = "2.0.0"

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"

VERDICTS = (PASS, FAIL, BLOCKED)

#: The only verdict an agent is permitted to assert. Everything else is derived
#: by :mod:`holoqa.verdict` from evidence HoloQA captured itself.
AGENT_ASSERTABLE = (BLOCKED,)
