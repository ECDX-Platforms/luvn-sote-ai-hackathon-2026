#!/usr/bin/env python
"""Derive the committed form-knowledge snapshot from the challenge's own form definitions.

Reads a directory of form JSONs (the extracted `Esitieto_lomakkeet_hackathon_json/` from the event's data
release) and writes `backend/app/knowledge_forms_v1.json`: Finnish wording only, unedited, with the sha256 of
every source file and the byte-identical duplicates it dropped. Deterministic: the same source bytes produce
the same snapshot bytes, so a re-run that changes nothing changes nothing on disk.

The source archive and the extracted directory are NOT committed; only this script and its output are.

    python scripts/import_form_knowledge.py [--source DIR] [--out FILE]

--source defaults to the extracted Esitieto_lomakkeet_hackathon_json/ under /tmp/luvn-forms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/tmp/luvn-forms/Esitieto_lomakkeet_hackathon_json")
DEFAULT_OUT = BACKEND / "app" / "knowledge_forms_v1.json"
SNAPSHOT_VERSION = "luvn-form-knowledge-v1"
RETRIEVED = "2026-10-13"


def _fi(node: object) -> str:
    """The Finnish text of a `{fi, sv, en}` node, or '' when absent. Never falls back to another language."""
    if isinstance(node, dict):
        value = node.get("fi")
        return value if isinstance(value, str) else ""
    return ""


def build(source: Path) -> tuple[dict, dict]:
    """Return (snapshot, summary). Raises SystemExit on a source file that breaks the expected shape."""
    files = sorted(source.glob("*.json"))
    if not files:
        raise SystemExit(f"no form JSONs under {source}")

    records: list[dict] = []
    seen: dict[str, str] = {}  # sha256 -> name of the file kept
    duplicates: list[dict] = []
    forms: list[dict] = []
    omissions: list[dict] = []
    questions_total = 0

    for path in files:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            records.append({"name": path.name, "sha256": digest, "duplicate_of": seen[digest]})
            duplicates.append({"name": path.name, "duplicate_of": seen[digest]})
            continue
        seen[digest] = path.name
        records.append({"name": path.name, "sha256": digest})

        try:
            form = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path.name}: not valid JSON ({error})") from error
        categories = [
            {"id": str(c["categoryId"]), "name_fi": _fi(c.get("name"))} for c in form.get("categories", [])
        ]
        category_ids = {c["id"] for c in categories}
        questions: list[dict] = []
        for q in form.get("questions", []):
            questions_total += 1
            text = _fi(q.get("questionText"))
            if not text.strip():
                omissions.append({"form_id": form["surveyGuid"], "question_id": q["questionId"]})
                continue
            category_id = str(q["categoryId"]) if q.get("categoryId") is not None else None
            if category_id is not None and category_id not in category_ids:
                raise SystemExit(f"{path.name}: question {q['questionId']} names unknown category {category_id}")
            # The answer options, in the form's own order, each with the points the form's own `scores` list gives
            # it (None when the question is not scored or the option has no entry). The instrument's arithmetic is
            # the form's, copied; nothing is authored here.
            points_by_answer: dict[str, int] = {}
            for entry in q.get("scores") or []:
                if isinstance(entry, dict) and isinstance(entry.get("points"), int) and entry.get("answerId"):
                    points_by_answer[str(entry["answerId"])] = int(entry["points"])
            answers = []
            for answer in q.get("answers") or []:
                if not isinstance(answer, dict) or not answer.get("answerId"):
                    continue
                answers.append({"id": str(answer["answerId"]), "text_fi": _fi(answer.get("answerText")),
                                "points": points_by_answer.get(str(answer["answerId"]))})
            questions.append({
                "id": str(q["questionId"]),
                "category_id": category_id,
                "text_fi": text,
                "type": str(q.get("type") or ""),
                "is_optional": bool(q.get("isOptional", False)),
                "scoring_enabled": bool(q.get("scoringEnabled", False)),
                "answers": answers,
            })
        # The form's own score definition: function, range and the publisher's summary bands (empty in this
        # release for every form; carried as the source has it).
        score_calculations = []
        for calc in form.get("scoreCalculations") or []:
            if not isinstance(calc, dict):
                continue
            summaries = []
            for summary in calc.get("summaries") or []:
                if isinstance(summary, dict):
                    summaries.append({"min": summary.get("min"), "max": summary.get("max"),
                                      "text_fi": _fi(summary.get("text") or summary.get("summary") or summary.get("title"))})
            score_calculations.append({"function": str(calc.get("calculationFunction") or ""),
                                       "min": calc.get("min"), "max": calc.get("max"),
                                       "title_fi": _fi(calc.get("title")), "summaries": summaries})
        forms.append({
            "form_id": str(form["surveyGuid"]),
            "revision_id": str(form["revisionGuid"]),
            "name_fi": _fi(form.get("name")),
            "description_fi": _fi(form.get("description")),
            "schema_version": str(form.get("schemaVersion") or ""),
            "categories": categories,
            "questions": questions,
            "score_calculations": score_calculations,
        })

    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "source": {
            "title": "SOTE AI Hackathon data — esitietolomakkeet ja kyselyt",
            "archive": "Hackathon_data.zip (Esitieto_lomakkeet_hackathon_json/)",
            "retrieved": RETRIEVED,
            "note": "Synthetic hackathon release. Form definitions as published to the event; text is the "
                    "form's own Finnish wording, unedited.",
            "files": records,
            "duplicates_dropped": duplicates,
        },
        "forms": forms,
    }
    summary = {
        "source_files": len(files),
        "forms": len(forms),
        "questions": sum(len(f["questions"]) for f in forms),
        "questions_in_source": questions_total,
        "duplicates": len(duplicates),
        "omissions": len(omissions),
    }
    return snapshot, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive the form-knowledge snapshot from the form JSONs.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="directory of form JSONs (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    snapshot, summary = build(args.source)
    text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    written = len(text.encode("utf-8"))
    print(
        f"forms={summary['forms']} questions={summary['questions']} "
        f"(in source {summary['questions_in_source']}) duplicates={summary['duplicates']} "
        f"omissions={summary['omissions']} source_files={summary['source_files']} "
        f"bytes_written={written} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
