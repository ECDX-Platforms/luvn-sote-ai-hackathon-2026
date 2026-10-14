"""Action suggestions as an **optional module**, deliberately outside the core decision.

Why this is a separate module and not three lines in `decide.py`. The core's job is to
assemble the lawful set, run the decision tables, and stop. Producing "what to do next"
is a different claim from "what is", and on any honest MDR reading it is the part of
this system closest to clinical decision support. Keeping it in its own stage, reading
only the core's finished output and feeding nothing back into it, means:

* the core alone ships a materially simpler position — the question becomes "do we
  switch this module on", not "is the whole system a device";
* the module is switchable without changing a single thing the core does, and the
  briefing degrades to the situation and risk rows plus the records, which is still a
  complete briefing;
* the boundary is the same one rule 7 of the team contract draws for every optional
  module: it receives the core's output and nothing else. This module takes three scalars
  (`situation_type`, `risk_level`, `risk_rule_id`) and returns a list of strings. It never sees a record,
  a persona, a `Scoped`, or a database handle, so it cannot leak what it was never given.
  The third is the id of the risk rule that matched. It is the core's conclusion about the records, not
  data about the person, which is why it may cross the boundary: it lets a step be keyed to what the core
  found, and it is what makes that step traceable to a named rule.

**What these strings are, and are not.** They come from `tables/actions.json`, which is
marked illustrative and is **not** clinical guidance. They are suggestions for the
professional to read and ignore; they trigger nothing, and no accept/dismiss control
exists because the core has not decided anything for anyone to accept.

**Default.** `LUVN_ACTIONS_MODULE` enables the module. It defaults to **on**, so the
shipped demo is unchanged in what it shows; set it to `0` to run the core alone. The
architecture is what makes the module safe, not the default — but a deployment that
wants the simpler regulatory posture flips one variable rather than editing code.
"""

from __future__ import annotations

import os

from .decide import evaluate_table
from .policy import table

MODULE_VERSION = "luvn-actions-module-v2"

# The literal a deployment sets to switch the module off ("0", "false", "no", "off").
_FALSEY = frozenset({"0", "false", "no", "off", ""})


def enabled() -> bool:
    """Whether the optional actions module runs in this process.

    Read per call rather than cached at import, so a test can flip it inside one process
    and the route's own tests can pin both states.
    """
    raw = os.environ.get("LUVN_ACTIONS_MODULE")
    if raw is None:
        return True
    return raw.strip().casefold() not in _FALSEY


def suggest_actions(situation_type: str, risk_level: str,
                    risk_rule_id: str | None = None) -> tuple[list[str], dict]:
    """Evaluate the actions table on the core's two outputs.

    Returns `(actions, trace_entry)`. The caller decides whether to include the trace
    entry; it is returned either way so the module's reason for a choice is auditable
    when it runs.

    Deliberately *not* a function of `features`: keying on the whole feature vector would
    quietly re-import the patient-level reasoning the core already did, and the module is
    supposed to consume a finished verdict, not re-derive one.
    """
    rule, matched = evaluate_table(table("actions"), {"situation_type": situation_type,
                                                      "risk_level": risk_level,
                                                      "risk_rule_id": risk_rule_id})
    actions = [str(action) for action in rule["output"].get("actions", [])]
    return actions, {"table": "actions", "rule_id": rule["rule_id"],
                     "output": rule["output"], "matched": matched}
