"""Fixture loader. The seam between "where records come from" and the rest.

Only the four source-keyed bound demo personas are file-backed. Swapping them
for a real Veil.AI export means changing load_personas().
"""

from pathlib import Path

from .upm import Persona

BOUND_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "experimental" / "bound-personas"
# Evaluator-only personas for the F2 value inventory; never loaded by the product.
DECISION_EVAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "experimental" / "decision-eval-personas"


def load_personas() -> list[Persona]:
    return [Persona.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(BOUND_DATA_DIR.glob("*.json"))]


def load_decision_eval_personas() -> list[Persona]:
    return [Persona.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(DECISION_EVAL_DATA_DIR.glob("*.json"))]
