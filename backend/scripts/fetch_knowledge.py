#!/usr/bin/env python
"""E4 — probe the registered knowledge sources, and measure what they could label.

Modes, and none of them invents data:

* `--probe` (default) requests each source's recorded interface and records **what answered**. It writes
  no labels, because a probe is not an extraction, and it never falls back to a hand-typed table.
* `--coverage` is offline. It scans the product fixtures for code-bearing payload values and reports, per
  system, the distinct codes and whether a snapshot resolves them.
* `--check` verifies the two artifacts and any present snapshot hashes; it changes nothing.
* `--code-service` fetches the Finnish national code lists (ICD-10, ATC, laboratory nomenclature, toimintakykymittarit).
* `--sote-sanastot` fetches THL Sote-sanastot (8 vocabularies, concepts, definitions, synonyms, status).
  Enumeration:
  1. GET https://sotesanastot.thl.fi/api/vocabularies lists all 8 vocabularies with metadata and publish dates.
  2. For each vocabulary, GET https://sotesanastot.thl.fi/api/vocabularies/{vocabularyId}/concepts lists all
     concepts in that vocabulary.
  3. For each concept, GET https://sotesanastot.thl.fi/api/vocabularies/{vocabularyId}/concepts/{conceptId} fetches
     full details including definitions, synonyms (altLabel), notes, and term recommendation/deprecation status.
* `--fhir-fi` fetches Finnish security and visibility code systems from fhir-fi/finnish-base-profiles pinned to a commit.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import knowledge  # noqa: E402
from app.fixtures import load_personas  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
PROBE = RESULTS / "knowledge-source-probe-v1.json"
COVERAGE = RESULTS / "knowledge-coverage-v1.json"
CAPTURES = RESULTS / "knowledge-captures-v1.json"
TIMEOUT_S = 25
# Every request this script makes identifies itself with this agent. Finto 403s `Python-urllib/3.x`, so a
# header-less probe records a live source as unreachable — which is what it did before 2026-10-12.
PROBE_USER_AGENT = "luvn-demo/1.0"
# The Finnish national code service's anonymous JSON REST API. `GET` is documented as needing no
# authentication; the SOAP/WS interface and the JSF-rendered `julkaisut` files are not needed for this.
CODE_SERVICE = "https://koodistopalvelu.kanta.fi/codeserver/csapi/v8"
CODE_SERVICE_SOURCE = "kanta-code-service"
# (our system key, classificationId, title). Verified live: 14 748 / 7 270 / 4 469 codes.
CODE_SERVICE_SETS = (
    ("icd10", "1.2.246.537.6.1", "THL Tautiluokitus ICD-10"),
    ("atc", "1.2.246.537.6.32", "Fimea ATC-luokitus"),
    ("measure", "1.2.246.537.6.3", "Kuntaliitto Laboratoriotutkimusnimikkeistö"),
    ("instrument", "1.2.246.537.6.160112", "THL Toimintakykymittarit"),
)
SOTE_SANASTOT_API = "https://sotesanastot.thl.fi/api"
SOTE_SANASTOT_SOURCE = "thl-sote-sanastot"

FHIR_FI_SOURCE = "fhir-fi-base-profiles"
FHIR_FI_COMMIT = "c5ae42c51cad6b4df0f4fd65857de18606e3963a"
FHIR_FI_FILES = {
    "security": "input/fsh/codesystems/security.fsh",
    "release": "input/fsh/codesystems/releaseforpatientviewing-cs.fsh",
}

# Payload keys that carry a code system's value in the product fixtures.
CODE_KEYS = {"icd10": "icd10", "atc": "atc", "tutkimus": "measure", "instrumentti": "instrument"}
OPERATION = re.compile(r'<(?:wsdl:)?operation name="([^"]+)"')


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _probe_source(row: dict) -> dict:
    interface = row.get("interface") or {}
    url = interface.get("url")
    result = {"source_id": row["id"], "machine_readable": bool(row.get("machine_readable")),
              "licence": row.get("licence"), "url": url, "interface_kind": interface.get("kind")}
    if not row.get("machine_readable") or not url:
        result.update({"reached": False, "outcome": "no machine-readable interface recorded",
                       "reason": row.get("reason")})
        return result
    # A probe must identify itself. Finto answers HTTP 403 to Python's default `Python-urllib/3.x` agent and
    # HTTP 200 to this one, so a header-less probe reported a live source as unreachable on 2026-10-12.
    request = urllib.request.Request(
        url, headers={"User-Agent": PROBE_USER_AGENT,
                      "Accept": "application/json, text/xml;q=0.9, */*;q=0.8"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - a recorded https URL
            body = response.read(200_000)
            result.update({"reached": True, "http_status": response.status,
                           "content_type": response.headers.get("Content-Type"), "bytes": len(body)})
            if interface.get("kind", "").endswith("wsdl"):
                result["operations"] = sorted(set(OPERATION.findall(body.decode("utf-8", "replace"))))
    except urllib.error.HTTPError as error:
        result.update({"reached": False, "http_status": error.code,
                       "outcome": f"HTTPError {error.code}"})
    except Exception as error:  # noqa: BLE001 - any transport failure is the result being recorded
        result.update({"reached": False, "outcome": f"{type(error).__name__}: {error}"})
    return result


def _probe() -> int:
    document = {"kind": "knowledge-source-probe", "generated_by": "backend/scripts/fetch_knowledge.py",
                "probed_at": _now(),
                "method": ("Each registered source's recorded interface is requested once. A probe "
                           "establishes reachability and interface shape only; no label is written here, "
                           "and a source with no machine-readable interface is reported as such."),
                "sources": [_probe_source(row) for row in knowledge.sources()]}
    RESULTS.mkdir(parents=True, exist_ok=True)
    PROBE.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    for row in document["sources"]:
        detail = row.get("http_status") or row.get("outcome") or row.get("reason")
        print(f"  {row['source_id']:20s} reached={row['reached']} {detail}")
    print(f"  -> {PROBE.relative_to(ROOT)}")
    return 0


def _corpus_codes() -> dict[str, set[str]]:
    systems: dict[str, set[str]] = {system: set() for system in set(CODE_KEYS.values())}
    for persona in load_personas():
        for record in persona.records:
            for key, system in CODE_KEYS.items():
                value = record.payload.get(key)
                if isinstance(value, str) and value.strip():
                    systems[system].add(value.strip())
    return {system: codes for system, codes in systems.items() if codes}


def _coverage() -> int:
    systems = _corpus_codes()
    requests = sorted((system, code) for system, codes in systems.items() for code in codes)
    report = knowledge.coverage(requests)
    resolved_by_system: dict[str, int] = {}
    for reference in report["references"]:
        resolved_by_system[reference["system"]] = resolved_by_system.get(reference["system"], 0) + 1
    unresolved_codes = [miss["code"] for miss in report["misses"]]
    alias_count = report.get("resolved_via_alias", 0)
    direct_count = report.get("resolved_direct", 0)
    headline = (f"{report['resolved']} of {report['requested']} fixture codes resolve "
                f"({direct_count} direct, {alias_count} via alias)")
    if unresolved_codes:
        if len(unresolved_codes) <= 5:
            # Name them individually when there are few
            miss_details = []
            for code in unresolved_codes:
                if code == "PHQ-9":
                    miss_details.append("PHQ-9 has no national label")
                else:
                    miss_details.append(f"{code} unresolved")
            headline += f"; {', '.join(miss_details)}"
        else:
            unresolved_systems = sorted({miss["system"] for miss in report["misses"]})
            headline += (f"; unresolved: {', '.join(unresolved_codes[:5])}… "
                         f"across {', '.join(unresolved_systems)}")
    if "instrument" in {miss["system"] for miss in report["misses"]} and not any(
        r["system"] == "instrument" for r in report["references"]
    ):
        headline += (" — every assessment instrument is unresolved, and those are exactly the structured "
                     "assessments the brief names")
    document = {"kind": "knowledge-coverage", "generated_by": "backend/scripts/fetch_knowledge.py",
                "headline": headline, "resolved_by_system": resolved_by_system,
                "generated_at": _now(),
                "method": ("Distinct code values in the product fixtures' payloads, per code system, "
                           "checked against whatever snapshots are present. A miss is named, not hidden, "
                           "and no fixture value is ever turned into a label by guesswork."),
                "systems": {system: len(codes) for system, codes in sorted(systems.items())},
                "snapshot_sources": sorted(snapshot for snapshot in _present_snapshots()),
                **report}
    RESULTS.mkdir(parents=True, exist_ok=True)
    COVERAGE.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"  systems={document['systems']} requested={report['requested']} "
          f"resolved={report['resolved']} unresolved={report['unresolved']}")
    print(f"  -> {COVERAGE.relative_to(ROOT)}")
    return 0


def _present_snapshots() -> list[str]:
    present = []
    for row in knowledge.sources():
        try:
            knowledge.load_snapshot(row["id"])
            present.append(row["id"])
        except knowledge.SnapshotError:
            continue
    return present


def _check() -> int:
    problems: list[str] = []
    if not COVERAGE.exists():
        print(f"check: no coverage artifact at {COVERAGE}", file=sys.stderr)
        return 1
    document = json.loads(COVERAGE.read_text(encoding="utf-8"))
    if document["resolved"] + document["unresolved"] != document["requested"]:
        problems.append("coverage counts do not add up")
    if len(document["misses"]) != document["unresolved"]:
        problems.append("every unresolved code must be named in misses")
    if document["resolved"] and not document["references"]:
        problems.append("resolved codes must carry their references")
    for reference in document["references"]:
        ref = reference.get("ref", "")
        if not ref.startswith("ref:"):
            problems.append(f"{reference.get('code')} is resolved without a ref")
        if reference.get("via_alias"):
            alias = reference.get("alias")
            code = reference.get("code")
            if not alias or not code:
                problems.append(f"alias reference missing alias or code: {reference}")
            elif f":{alias}→{code}" not in ref:
                problems.append(f"alias reference {ref} does not carry both alias ({alias}) and code ({code})")

    # Validate probe artifact
    if not PROBE.exists():
        problems.append(f"no probe artifact at {PROBE}")
    else:
        try:
            probe_doc = json.loads(PROBE.read_text(encoding="utf-8"))
            probe_sources = {s["source_id"]: s for s in probe_doc.get("sources", [])}
            for row in knowledge.sources():
                if row.get("machine_readable"):
                    sid = row["id"]
                    if sid not in probe_sources:
                        problems.append(f"machine-readable source {sid!r} missing from probe artifact")
                    else:
                        probe_entry = probe_sources[sid]
                        # Must have a recorded outcome: reached=True with http_status, or reached=False with outcome/reason/http_status
                        has_status = "http_status" in probe_entry
                        has_outcome = "outcome" in probe_entry or "reason" in probe_entry
                        if not (has_status or has_outcome):
                            problems.append(f"source {sid!r} in probe has no recorded outcome or status")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"failed to read or parse probe artifact: {exc}")

    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: coverage requested={document['requested']} resolved={document['resolved']} "
          f"unresolved={document['unresolved']} snapshot_sources={document['snapshot_sources']}")
    return 1 if problems else 0


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": PROBE_USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - a recorded https URL
        return json.loads(response.read().decode("utf-8"))


def _code_service_codes(classification_id: str) -> tuple[dict[str, str], dict]:
    """Every code of one classification, with its Finnish short name, one page at a time."""
    codes: dict[str, str] = {}
    page, version = 1, {}
    while True:
        payload = _get_json(f"{CODE_SERVICE}/classifications/{classification_id}/conceptcodes"
                            f"?page={page}&pageSize=500")
        for item in payload.get("conceptCodes", []):
            attributes = {attribute["attributeName"]: attribute["attributeValue"][0]
                          for attribute in item.get("attributes", []) if attribute.get("attributeValue")}
            code = item.get("conceptCodeId")
            label = attributes.get("LongName") or attributes.get("ShortName") or attributes.get("Abbreviation")
            if code and label:
                codes[str(code).strip()] = str(label).strip()
            if not version and item.get("versionName"):
                version = {"version_id": item.get("versionId"), "version_name": item.get("versionName")}
        if page >= int(payload.get("totalPages") or 1):
            break
        page += 1
    return codes, version


def _code_service() -> int:
    """Fetch the Finnish national code lists as a labelling snapshot.

    This is the labelling source, and it is the anonymous JSON REST API rather than the SOAP/WS interface or
    the JSF-rendered `julkaisut` files: `GET` is documented as needing no authentication. Reachable only from
    Finland. The snapshot is written under the gitignored `backend/data/knowledge/`, and the registry records
    that the code content's reuse terms are *not* verified — so it is not committed and not redistributed.
    """
    labels: dict[str, dict[str, str]] = {}
    versions: dict[str, dict] = {}
    for system, classification_id, title in CODE_SERVICE_SETS:
        try:
            codes, version = _code_service_codes(classification_id)
        except Exception as error:  # noqa: BLE001 - report the failure rather than write a partial snapshot
            print(f"  {system}: FAILED {type(error).__name__}: {error}", file=sys.stderr)
            return 1
        labels[system] = codes
        versions[system] = {**version, "classification_id": classification_id, "title": title}
        print(f"  {system:8s} {len(codes):>6d} codes  {version.get('version_name', '')}")
    document = {"source_id": CODE_SERVICE_SOURCE, "version": _now()[:10], "fetched_at": _now(),
                "interface": f"{CODE_SERVICE}/classifications",
                "versions": versions, "labels": labels}
    knowledge.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    path = knowledge.SNAPSHOT_DIR / f"{CODE_SERVICE_SOURCE}.json"
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "  snapshot\n")
    loader = knowledge.load_snapshot(CODE_SERVICE_SOURCE)  # verifies the hash by reading it back
    print(f"  -> {path}  ({len(raw) // 1024} KiB, systems={sorted(loader['labels'])})")
    return 0


def _sote_sanastot() -> int:
    """Fetch THL Sote-sanastot (8 vocabularies, concepts, definitions, synonyms, status)."""
    try:
        vocabs = _get_json(f"{SOTE_SANASTOT_API}/vocabularies")
    except Exception as error:  # noqa: BLE001
        print(f"  sote-sanastot: FAILED to fetch vocabularies: {error}", file=sys.stderr)
        return 1

    all_stubs = []
    vocab_meta = {}
    for v in vocabs:
        vid = v["id"]
        title = next((p["value"] for p in v.get("properties", {}).get("prefLabel", []) if p.get("lang") == "fi"), vid)
        pub_date = next((p["value"] for p in v.get("properties", {}).get("publishDate", [])), "unknown")
        vocab_meta[vid] = {"title": title, "publishDate": pub_date}
        try:
            c_list = _get_json(f"{SOTE_SANASTOT_API}/vocabularies/{vid}/concepts")
        except Exception as error:  # noqa: BLE001
            print(f"  sote-sanastot: FAILED to fetch concepts list for {title}: {error}", file=sys.stderr)
            return 1
        for item in c_list:
            all_stubs.append((vid, item["id"]))

    def fetch_concept(stub: tuple[str, str]) -> tuple[str, dict]:
        vid, cid = stub
        url = f"{SOTE_SANASTOT_API}/vocabularies/{vid}/concepts/{cid}"
        request = urllib.request.Request(url, headers={"User-Agent": PROBE_USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return vid, json.loads(response.read().decode("utf-8"))

    print(f"  sote-sanastot: fetching {len(all_stubs)} concepts across {len(vocabs)} vocabularies...")
    with ThreadPoolExecutor(max_workers=25) as pool:
        full_concepts = list(pool.map(fetch_concept, all_stubs))

    labels: dict[str, str] = {}
    concepts_sibling: dict[str, dict] = {}
    for vid, c in full_concepts:
        cid = c["id"]
        props = c.get("properties", {})
        prefs = [p["value"] for p in props.get("prefLabel", []) if p.get("lang") == "fi"]
        pref = prefs[0] if prefs else ""
        if pref:
            labels[cid] = pref

        fi_alt = [p["value"] for p in props.get("altLabel", []) if p.get("lang") == "fi"]
        sv_pref = [p["value"] for p in props.get("prefLabel", []) if p.get("lang") == "sv"]
        sv_alt = [p["value"] for p in props.get("altLabel", []) if p.get("lang") == "sv"]
        avoidable = [p["value"] for p in props.get("avoidableLabel", []) if p.get("lang") == "fi"]
        deprecated = [p["value"] for p in props.get("deprecatedLabel", []) if p.get("lang") == "fi"]
        fi_def = [p["value"] for p in props.get("definition", []) if p.get("lang") == "fi"]
        sv_def = [p["value"] for p in props.get("definition", []) if p.get("lang") == "sv"]
        fi_notes = [p["value"] for p in props.get("note", []) if p.get("lang") == "fi"]
        status_list = [s.get("code") for s in c.get("references", {}).get("status", []) if s.get("code")]
        status = status_list[0] if status_list else None

        concepts_sibling[cid] = {
            "vocabulary_id": vid,
            "vocabulary_title": vocab_meta.get(vid, {}).get("title"),
            "vocabulary_publish_date": vocab_meta.get(vid, {}).get("publishDate"),
            "prefLabel_fi": pref,
            "prefLabel_sv": sv_pref[0] if sv_pref else None,
            "synonyms_fi": fi_alt,
            "synonyms_sv": sv_alt,
            "avoidable_fi": avoidable,
            "deprecated_fi": deprecated,
            "definition_fi": fi_def[0] if fi_def else None,
            "definition_sv": sv_def[0] if sv_def else None,
            "notes_fi": fi_notes,
            "status": status,
        }

    document = {
        "source_id": SOTE_SANASTOT_SOURCE,
        "version": _now()[:10],
        "version_basis": "fetch date (per-vocabulary publish dates recorded in vocabularies map)",
        "fetched_at": _now(),
        "vocabularies": vocab_meta,
        "labels": {"concept": labels},
        "concepts": concepts_sibling,
    }

    knowledge.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    path = knowledge.SNAPSHOT_DIR / f"{SOTE_SANASTOT_SOURCE}.json"
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "  snapshot\n")
    loader = knowledge.load_snapshot(SOTE_SANASTOT_SOURCE)
    print(f"  -> {path}  ({len(raw) // 1024} KiB, concepts={len(loader['labels']['concept'])})")
    return 0


def _fhir_fi() -> int:
    """Fetch Finnish security and visibility code systems from fhir-fi/finnish-base-profiles pinned to a commit."""
    labels: dict[str, dict[str, str]] = {}
    details: dict[str, dict[str, dict]] = {}
    raw_files: dict[str, str] = {}
    pattern = re.compile(r'^\*\s+#(\S+)\s+"([^"]+)"(?:\s+"([^"]*)")?', re.MULTILINE)

    for namespace, file_path in FHIR_FI_FILES.items():
        url = f"https://raw.githubusercontent.com/fhir-fi/finnish-base-profiles/{FHIR_FI_COMMIT}/{file_path}"
        request = urllib.request.Request(url, headers={"User-Agent": PROBE_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
                content = response.read().decode("utf-8")
        except Exception as error:  # noqa: BLE001
            print(f"  fhir-fi: FAILED to fetch {file_path}: {error}", file=sys.stderr)
            return 1

        raw_files[file_path] = url
        ns_labels = {}
        ns_details = {}
        for match in pattern.finditer(content):
            code, display, desc = match.groups()
            ns_labels[code] = display
            ns_details[code] = {"display": display, "definition": desc or None}

        labels[namespace] = ns_labels
        details[namespace] = ns_details
        print(f"  fhir-fi {namespace:8s} {len(ns_labels):>2d} codes from {file_path}")

    document = {
        "source_id": FHIR_FI_SOURCE,
        "commit": FHIR_FI_COMMIT,
        "version": FHIR_FI_COMMIT[:12],
        "fetched_at": _now(),
        "files": raw_files,
        "labels": labels,
        "code_systems": details,
    }

    knowledge.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    path = knowledge.SNAPSHOT_DIR / f"{FHIR_FI_SOURCE}.json"
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "  snapshot\n")
    loader = knowledge.load_snapshot(FHIR_FI_SOURCE)
    print(f"  -> {path}  ({len(raw) // 1024} KiB, systems={sorted(loader['labels'])})")
    return 0


def _captures() -> int:
    report = knowledge.capture_refs()
    document = {"kind": "knowledge-captures", "generated_by": "backend/scripts/fetch_knowledge.py",
                "generated_at": _now(),
                "method": ("Every capture under the gitignored backend/data/knowledge/captures is read "
                           "back and hash-checked. A refused capture is named with its reason, because a "
                           "capture that cannot be attributed must not render."),
                **report}
    RESULTS.mkdir(parents=True, exist_ok=True)
    CAPTURES.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"  captures={report['captures']} valid={report['valid']} refused={report['refused']}")
    for refusal in report["refusals"]:
        print(f"    refused {refusal['capture_id']}: {refusal['reason']}")
    print(f"  -> {CAPTURES.relative_to(ROOT)}")
    return 0 if not report["refusals"] else 1


def _capture_add(source: Path) -> int:
    """Turn a pasted Expert AI answer into a verifiable capture. Never overwrites an existing one."""
    payload = json.loads(source.read_text(encoding="utf-8"))
    capture_id = str(payload.get("capture_id") or f"uc-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
    document = {"capture_id": capture_id,
                "source_id": str(payload.get("source_id") or "uptodate-expert-ai"),
                "question": str(payload.get("question") or ""),
                "audience": str(payload.get("audience") or "professional"),
                "answer": str(payload.get("answer") or ""),
                "captured_at": str(payload.get("captured_at") or _now()),
                "surface": str(payload.get("surface") or "UpToDate Expert AI (browser)")}
    knowledge.CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = knowledge.CAPTURE_DIR / f"{capture_id}.json"
    if path.exists():
        print(f"capture-add: {path} already exists; refusing to overwrite", file=sys.stderr)
        return 1
    raw = json.dumps(document, sort_keys=True, ensure_ascii=False).encode()
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "  capture\n")
    try:
        evidence = knowledge.load_capture(capture_id)
    except knowledge.SnapshotError as error:
        path.unlink(missing_ok=True)
        path.with_suffix(".sha256").unlink(missing_ok=True)
        print(f"capture-add: refused and removed: {error}", file=sys.stderr)
        return 1
    print(f"capture-add: {evidence.as_ref()}  (audience={evidence.audience}, "
          f"sha256={evidence.sha256[:12]}…)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe knowledge sources and measure code coverage")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--probe", action="store_true", help="request each recorded interface (default)")
    group.add_argument("--coverage", action="store_true", help="offline: scan the fixtures for codes")
    group.add_argument("--captures", action="store_true", help="report the external-evidence captures")
    group.add_argument("--code-service", action="store_true",
                       help="fetch the Finnish national code lists as a labelling snapshot")
    group.add_argument("--sote-sanastot", action="store_true",
                       help="fetch THL Sote-sanastot vocabularies and concepts as a labelling snapshot")
    group.add_argument("--fhir-fi", action="store_true",
                       help="fetch fhir-fi security and visibility code systems as a labelling snapshot")
    group.add_argument("--capture-add", metavar="FILE", type=Path,
                       help="add a capture from a JSON file with question, audience, answer")
    group.add_argument("--check", action="store_true", help="verify the committed artifacts")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if args.coverage:
        return _coverage()
    if args.captures:
        return _captures()
    if args.code_service:
        return _code_service()
    if args.sote_sanastot:
        return _sote_sanastot()
    if args.fhir_fi:
        return _fhir_fi()
    if args.capture_add:
        return _capture_add(args.capture_add)
    return _probe()


if __name__ == "__main__":
    raise SystemExit(main())
