"""Decision layer: evaluate JSON tables against triage features.

Rules decide; the LLM only narrates. The tables
are a deterministic function of `features` -- no randomness and no model call.

The policy cut and the table loading live in `policy.py`; this module operates on
a persona that is already cut to one role.
"""

from __future__ import annotations

from . import source
from .policy import Scoped, table
from .triage import extract_features

OPS = {
    "is_true": lambda v, t: bool(v),
    "is_false": lambda v, t: not bool(v),
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
    ">=": lambda v, t: v is not None and v >= t,
    "<=": lambda v, t: v is not None and v <= t,
    ">": lambda v, t: v is not None and v > t,
    "<": lambda v, t: v is not None and v < t,
    "non_empty": lambda v, t: bool(v),
    "contains": lambda v, t: isinstance(v, (list, set, tuple, str)) and t in v,
}


def _get(features: dict, path: str):
    cur = features
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match_rule(rule: dict, features: dict) -> list[dict] | None:
    """Return the matched predicates, or None if the rule does not fire."""
    matched: list[dict] = []
    for pred in rule.get("when", []):
        op = OPS.get(pred["op"])
        if op is None:
            raise ValueError(f"unknown op {pred['op']!r} in rule {rule.get('rule_id')!r}")
        value = _get(features, pred["field"])
        if not op(value, pred.get("value")):
            return None
        matched.append(
            {"field": pred["field"], "op": pred["op"], "expected": pred.get("value"), "actual": value}
        )
    return matched


def evaluate_table(table_def: dict, features: dict) -> tuple[dict, list[dict]]:
    """Highest-priority rule whose predicates all hold. First match wins."""
    for rule in sorted(table_def["rules"], key=lambda r: r["priority"], reverse=True):
        matched = _match_rule(rule, features)
        if matched is not None:
            return rule, matched
    raise ValueError(
        f"no rule matched in table {table_def.get('table', '?')!r}; "
        "every decision table needs a catch-all rule (when: [])"
    )


def decide(scoped: Scoped) -> dict:
    # Policy before rank: the caller already cut the persona to the role. The
    # tables run on the role-visible records only, so decisions cannot leak
    # hidden records.
    features = extract_features(scoped.persona)
    trace: list[dict] = []

    situation_rule, sit_matched = evaluate_table(table("situation"), features)
    risk_rule, risk_matched = evaluate_table(table("risk"), features)

    # The core proposes nothing. There is deliberately no `actions` key in this dict:
    # suggested next steps are an optional downstream module (`app.actions`), which reads
    # this function's own finished output and feeds nothing back into it. Adding a
    # suggestion here is how a core stops being a core.
    decision = {
        "persona_id": scoped.persona.persona_id,
        "role": scoped.role,
        "situation": situation_rule["output"],
        "risk": risk_rule["output"],
        "conflicts": features["conflicts"],
        "features": features,
        "trace": trace,
    }

    # Per-record provenance for the risk verdict above. This is what lets the view say
    # *why this record is in the risk picture* on the record itself, instead of printing
    # one patient-level label and leaving the reader to guess which record produced it.
    # It restates the rule that already fired -- never a new judgement, never an urgency
    # score, and never a second opinion about the patient.
    decision["risk_reasons_by_record"] = _risk_reasons_by_record(scoped, risk_rule["rule_id"], features)

    trace.extend(
        [
            {"table": "situation", "rule_id": situation_rule["rule_id"], "output": decision["situation"], "matched": sit_matched},
            {"table": "risk", "rule_id": risk_rule["rule_id"], "output": decision["risk"], "matched": risk_matched},
        ]
    )
    decision["visible_record_ids"] = list(scoped.ids)
    return decision


def _risk_reasons_by_record(scoped: Scoped, risk_rule_id: str, features: dict) -> dict[str, list[str]]:
    """The permitted records behind the risk row, keyed by record id.

    Only the rule that actually fired contributes, so this cannot add an interpretation the
    table did not make. A rule with no single-record provenance (an absence, a count, or a
    score assembled across records) contributes nothing: inventing a record for it would be
    exactly the "absence is never filled" failure the whole surface is built to avoid.
    """
    by_record: dict[str, list[str]] = {}

    def add(record_id: str | None, reason: str) -> None:
        if not record_id:
            return
        by_record.setdefault(record_id, []).append(reason)

    if risk_rule_id == "risk-laakitysristiriita":
        # Names the disagreement, not a side of it: this per-record reason is what the view
        # attaches to each of the two conflicting records, and asserting "active" or
        # "stopped" here would make one source's claim look like the finding itself.
        reason = "Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen"
        for conflict in features.get("conflicts", []):
            add(conflict.get("medication_record_id"), reason)
            add(conflict.get("note_record_id"), reason)
    elif risk_rule_id in {"risk-akuutti-vitaalit", "risk-happikyllasteisyys", "risk-hoitamattomuus"}:
        thresholds = {"risk-akuutti-vitaalit": (source.temperature, "Korkea kuume", lambda v: v >= 39),
                      "risk-happikyllasteisyys": (source.spo2, "Alentunut happikyllästeisyys", lambda v: v < 94),
                      "risk-hoitamattomuus": (source.missed_doses, "Merkittävä hoitamattomuus", lambda v: v >= 7)}
        reader, reason, fires = thresholds[risk_rule_id]
        for record in scoped.records:
            value = reader(record)
            if value is not None and fires(value):
                add(record.record_id, reason)

    return by_record
