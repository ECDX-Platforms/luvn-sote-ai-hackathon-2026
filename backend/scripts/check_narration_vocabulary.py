"""Deterministic, offline, model-free narration vocabulary check against official term classes.

Compares model-generated Finnish narration sentences against the term classes the
publisher of THL Sote-sanastot marks as discouraged or deprecated (`avoidable_fi`,
`deprecated_fi`). Also measures vocabulary coverage over the distinct content words
used in the narration.

No network, no model.
Snapshot-absent path exits cleanly with a clear message and exit code 0.

Stemming approximation, not morphology:
This matcher applies a deterministic stemming approximation to catch common Finnish
inflections and consonant gradation variants (kk→k, pp→p, tt→t, nt→nn, nk→ng, k→∅,
t→d, p→v). It is NOT a full morphological analyzer.
What it cannot catch:
  - Rare consonant gradations (e.g. mp→mm, lt→ll, rt→rr)
  - Vowel changes in stems (e.g. vesi -> veden, käsi -> käden)
  - Plural inflections of compound words where non-final components inflect
  - Derivational morphology (e.g. johtaa -> johtaja, hoitaa -> hoidollinen)
  - Nominal type changes (e.g. -us/-ys words where oblique stem ends in -kse-)
Honesty rule: a sentence with no hit is reported as not covered, never as passing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "backend" / "data" / "knowledge" / "thl-sote-sanastot.json"
DEFAULT_OUTPUT_PATH = ROOT / ".scratch" / "finnish-knowledge-sources" / "results" / "narration-vocabulary-2026-10-12.json"
RECORDED_ADJUDICATION_PATH = ROOT / ".scratch" / "retrieval-and-knowledge" / "results" / "verifier-adjudication-v1.json"
RECORDED_LIVE_TRIAL_PATH = ROOT / ".foundry" / "results" / "live-encounter-trial-v1.json"


def _relative(path: Path) -> str:
    """A repo-relative path for the artifact. Two oracles flagged the absolute developer path on 2026-10-12:
    a tracked result must not leak the author's filesystem, and it contradicts the clean-clone claim."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


ENCOUNTERS_29_SENTENCES = (
    "bound-encounter-002-laakitys-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-sairaanhoitaja",
    "bound-encounter-003-toimintakyky-sairaanhoitaja",
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija",
    "bound-encounter-005-laakitys-sairaanhoitaja",
    "bound-encounter-012-neuvola-terveydenhoitaja",
    "bound-encounter-009-laakitys-sairaanhoitaja",
    "bound-encounter-010-laakitys-sairaanhoitaja",
)

FINNISH_VOWELS = set("aeiouyäöAEIOUYÄÖ")
CONSONANT_GRADATIONS = (
    ("kk", "k"),
    ("pp", "p"),
    ("tt", "t"),
    ("nt", "nn"),
    ("nk", "ng"),
    ("k", ""),
    ("t", "d"),
    ("p", "v"),
)

# Standard Finnish closed-class grammatical items (pronouns, conjunctions, aux 'olla', negation, adpositions, units)
FINNISH_FUNCTION_WORDS = {
    # Conjunctions
    "ja", "sekä", "eli", "tai", "vai", "mutta", "vaan", "sillä",
    "että", "jotta", "koska", "kun", "jos", "vaikka", "kuin", "kunnes",
    # Pronouns
    "se", "sen", "sitä", "siinä", "siitä", "siihen", "sillä", "siltä", "sille",
    "ne", "niiden", "niitä", "niissä", "niistä", "niihin", "niillä", "niiltä", "niille",
    "tämä", "tämän", "tätä", "tässä", "tästä", "tähän", "tällä", "tältä", "tälle",
    "nämä", "näiden", "näitä", "näissä", "näistä", "näihin", "näillä", "näiltä", "näille",
    "tuo", "tuon", "tuota", "tuossa", "tuosta", "tuohon", "tuolla", "tuolta", "tuolle",
    "nuo", "noiden", "noita", "noissa", "noista", "noihin", "noilla", "noilta", "noille",
    "joka", "jonka", "jota", "jossa", "josta", "johon", "jolla", "jolta", "jolle",
    "jotka", "joiden", "joita", "joissa", "joista", "joihin", "joilla", "joilta", "joille",
    "kuka", "kenen", "ketä", "kenessä", "kenestä", "keneen", "kenellä", "keneltä", "kenelle",
    "mikä", "minkä", "mitä", "missä", "mistä", "mihin", "millä", "miltä", "mille",
    "hän", "hänen", "häntä", "hänessä", "hänestä", "häneen", "hänellä", "häneltä", "hänelle",
    "he", "heidän", "heitä", "heissä", "heistä", "heihin", "heillä", "heiltä", "heille",
    "minä", "minun", "minua", "minussa", "minusta", "minuun", "minulla", "minulta", "minulle",
    "sinä", "sinun", "sinua", "sinussa", "sinusta", "sinuun", "sinulla", "sinulta", "sinulle",
    "me", "meidän", "meitä", "meissä", "meistä", "meihin", "meillä", "meiltä", "meille",
    "te", "teidän", "teitä", "teissä", "teistä", "teihin", "teillä", "teiltä", "teille",
    "itse", "itsensä", "itseään",
    # Verb olla & negation ei
    "olla", "olen", "olet", "on", "olemme", "olette", "ovat",
    "olin", "olit", "oli", "olimme", "olitte", "olivat",
    "ole", "ollut", "olleet", "oltava", "oltu",
    "ei", "en", "et", "emme", "ette", "eivät",
    # Common adpositions / particles / numbers / units
    "myös", "vielä", "jo", "vain", "ainakin", "aina", "niin", "noin", "silloin",
    "kanssa", "mukaan", "vuoksi", "jälkeen", "sitten", "ilman", "kautta", "asti",
    "joten", "kuten",
    "mg", "kg", "cm", "f", "n",
}


def generate_surface_patterns(clean_term: str) -> list[str]:
    """Generate inflection-tolerant surface patterns for a term.

    Compound terms (e.g. sote-keskus) inflect on their last component.
    Produces:
      - The term itself (exact)
      - The term with final vowel removed + word-continuation suffix ([a-zA-ZåäöÅÄÖ]*)
      - Consonant gradation variants (kk→k, pp→p, tt→t, nt→nn, nk→ng, k→∅, t→d, p→v)
    """
    m = re.search(r"^(.*[\s\-])([^\s\-]+)$", clean_term)
    if m:
        prefix, last = m.group(1), m.group(2)
    else:
        prefix, last = "", clean_term

    stems = set()
    stems.add(last)
    if len(last) > 2 and last[-1] in FINNISH_VOWELS:
        stems.add(last[:-1])

    for s in list(stems):
        for strong, weak in CONSONANT_GRADATIONS:
            idx = s.lower().rfind(strong)
            if idx > 0:  # Medial consonant gradation
                grad = s[:idx] + weak + s[idx + len(strong):]
                stems.add(grad)
                if len(grad) > 2 and grad[-1] in FINNISH_VOWELS:
                    stems.add(grad[:-1])

    sub_patterns = [re.escape(clean_term)]
    for st in sorted(stems, key=len, reverse=True):
        full_stem = prefix + st
        p = re.escape(full_stem) + r"[a-zA-ZåäöÅÄÖ]*"
        if p not in sub_patterns:
            sub_patterns.append(p)

    return sub_patterns


def build_term_pattern(clean_term: str) -> re.Pattern:
    """Build a compiled case-insensitive regex matching whole words for the term."""
    sub_patterns = generate_surface_patterns(clean_term)
    combined = r"\b(?:" + "|".join(sub_patterns) + r")\b"
    return re.compile(combined, re.IGNORECASE)


def load_term_rules_from_snapshot(data: dict) -> dict:
    """Extract discouraged and deprecated terms from the parsed snapshot document.

    Returns a dict with:
      - terms_checked: count of raw term entries
      - distinct_terms: count of unique terms checked
      - concepts_covered: count of unique concepts with discouraged/deprecated terms
      - rules: list of rule dicts with keys:
          term, term_class, preferred, concept_id, vocabulary_title, regex, patterns
    """
    concepts = data.get("concepts") or {}
    rules = []
    seen_terms = set()
    concepts_with_terms = set()

    for cid, c in concepts.items():
        pref = c.get("prefLabel_fi") or ""
        vtitle = c.get("vocabulary_title") or ""
        avoidable = c.get("avoidable_fi") or []
        deprecated = c.get("deprecated_fi") or []

        for term in avoidable:
            if not isinstance(term, str) or not term.strip():
                continue
            concepts_with_terms.add(cid)
            seen_terms.add(term)
            clean_term = term.lstrip("> ").split("(")[0].strip()
            pattern = build_term_pattern(clean_term)
            rules.append({
                "raw_term": term,
                "clean_term": clean_term,
                "term_class": "avoidable_fi",
                "publisher_status": "Vältettävä termi",
                "preferred": pref,
                "concept_id": cid,
                "vocabulary_title": vtitle,
                "regex": pattern,
                "patterns": generate_surface_patterns(clean_term),
            })

        for term in deprecated:
            if not isinstance(term, str) or not term.strip():
                continue
            concepts_with_terms.add(cid)
            seen_terms.add(term)
            clean_term = term.lstrip("> ").split("(")[0].strip()
            pattern = build_term_pattern(clean_term)
            rules.append({
                "raw_term": term,
                "clean_term": clean_term,
                "term_class": "deprecated_fi",
                "publisher_status": "Hylättävä termi",
                "preferred": pref,
                "concept_id": cid,
                "vocabulary_title": vtitle,
                "regex": pattern,
                "patterns": generate_surface_patterns(clean_term),
            })

    return {
        "terms_checked": len(rules),
        "distinct_terms": len(seen_terms),
        "concepts_covered": len(concepts_with_terms),
        "rules": rules,
    }


def load_in_scope_sentences(
    adjudication_path: Path = RECORDED_ADJUDICATION_PATH,
    only_canonical_29: bool = True,
) -> list[dict]:
    """Load model-generated narration sentences from the recorded evaluation artifact.

    These sentences are model-generated prose (FreshNarration output), not verbatim
    source quotations.
    """
    if not adjudication_path.exists():
        return []
    data = json.loads(adjudication_path.read_text(encoding="utf-8"))
    sentences = []
    for enc in data.get("encounters", []):
        enc_id = enc.get("encounter_id", "")
        if only_canonical_29 and enc_id not in ENCOUNTERS_29_SENTENCES:
            continue
        for s in enc.get("sentences", []):
            sid = s.get("sentence_id", "")
            text = s.get("text", "")
            cited = s.get("cited_record_ids", [])
            sentences.append({
                "id": f"{enc_id}:{sid}",
                "encounter_id": enc_id,
                "sentence_id": sid,
                "text": text,
                "cited_record_ids": cited,
                "kind": "model_generated_prose",
            })
    return sentences


def check_sentences(sentences: list[dict], term_rules: list[dict]) -> tuple[list[dict], dict]:
    """Check a list of sentences against the term rules deterministically."""
    hits = []
    sentence_hits_map: dict[str, list[dict]] = {s["id"]: [] for s in sentences}

    for s in sentences:
        text = s["text"]
        for rule in term_rules:
            m = rule["regex"].search(text)
            if m:
                hit = {
                    "sentence_id": s["id"],
                    "encounter_id": s["encounter_id"],
                    "sentence_text": text,
                    "matched_surface": m.group(0),
                    "term": rule["raw_term"],
                    "term_class": rule["term_class"],
                    "publisher_status": rule["publisher_status"],
                    "preferred_alternative": rule["preferred"],
                    "concept_id": rule["concept_id"],
                    "vocabulary_title": rule["vocabulary_title"],
                }
                hits.append(hit)
                sentence_hits_map[s["id"]].append(hit)

    sentences_with_hits = sum(1 for s_hits in sentence_hits_map.values() if len(s_hits) > 0)
    total_sentences = len(sentences)
    hits_per_sentence = round(len(hits) / total_sentences, 4) if total_sentences > 0 else 0.0

    stats = {
        "sentences_in_scope": total_sentences,
        "sentences_with_hit": sentences_with_hits,
        "sentences_not_covered": total_sentences - sentences_with_hits,
        "total_hits": len(hits),
        "hits_per_sentence": hits_per_sentence,
    }
    return hits, stats


def extract_snapshot_words(snapshot_data: dict) -> set[str]:
    """Extract distinct lowercase words across all term labels in the snapshot."""
    words = set()
    for c in snapshot_data.get("concepts", {}).values():
        labels = []
        if c.get("prefLabel_fi"):
            labels.append(c["prefLabel_fi"])
        labels.extend(c.get("synonyms_fi") or [])
        labels.extend(c.get("avoidable_fi") or [])
        labels.extend(c.get("deprecated_fi") or [])
        for label in labels:
            if not isinstance(label, str):
                continue
            clean = label.lstrip("> ").split("(")[0].strip().lower()
            for w in re.findall(r"\b[a-zA-ZåäöÅÄÖ]+\b", clean):
                words.add(w)
    return words


def measure_vocabulary_coverage(sentences: list[dict], snapshot_words: set[str]) -> dict:
    """Measure how many distinct content words in the sentences appear in the vocabulary."""
    all_text = " ".join(s["text"] for s in sentences)
    raw_tokens = re.findall(r"\b[a-zA-ZåäöÅÄÖ]+\b", all_text.lower())
    content_tokens = [w for w in raw_tokens if w not in FINNISH_FUNCTION_WORDS and len(w) > 1]
    token_counts = Counter(content_tokens)
    distinct_content_words = sorted(set(content_tokens))

    covered = [w for w in distinct_content_words if w in snapshot_words]
    uncovered = [w for w in distinct_content_words if w not in snapshot_words]
    uncovered_ranked = sorted(uncovered, key=lambda w: (-token_counts[w], w))[:20]

    total_distinct = len(distinct_content_words)
    covered_count = len(covered)
    coverage_fraction = round(covered_count / total_distinct, 4) if total_distinct > 0 else 0.0

    return {
        "narration_content_words": total_distinct,
        "covered_by_vocabulary": covered_count,
        "coverage_fraction": coverage_fraction,
        "top_20_uncovered_words": uncovered_ranked,
    }


def run_check(
    snapshot_path: Path = SNAPSHOT_PATH,
    adjudication_path: Path = RECORDED_ADJUDICATION_PATH,
    output_path: Path | None = DEFAULT_OUTPUT_PATH,
    only_canonical_29: bool = True,
) -> dict:
    """Run vocabulary check and optionally write output artifact."""
    if not snapshot_path.exists():
        print(f"snapshot absent: no file at {snapshot_path}")
        return {
            "status": "snapshot_absent",
            "message": f"Snapshot absent at {snapshot_path}; exiting cleanly.",
            "exit_code": 0,
        }

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rule_info = load_term_rules_from_snapshot(data)
    sentences = load_in_scope_sentences(adjudication_path, only_canonical_29=only_canonical_29)
    hits, stats = check_sentences(sentences, rule_info["rules"])

    snapshot_words = extract_snapshot_words(data)
    coverage_stats = measure_vocabulary_coverage(sentences, snapshot_words)

    result = {
        "status": "completed",
        "snapshot_path": _relative(snapshot_path),
        "terms_checked": rule_info["terms_checked"],
        "distinct_terms_checked": rule_info["distinct_terms"],
        "concepts_covered": rule_info["concepts_covered"],
        "sentences_in_scope": stats["sentences_in_scope"],
        "sentences_with_hit": stats["sentences_with_hit"],
        "sentences_not_covered": stats["sentences_not_covered"],
        "total_hits": stats["total_hits"],
        "hits_per_sentence": stats["hits_per_sentence"],
        "in_scope_sentence_ids": [s["id"] for s in sentences],
        "hits": hits,
        "narration_content_words": coverage_stats["narration_content_words"],
        "covered_by_vocabulary": coverage_stats["covered_by_vocabulary"],
        "coverage_fraction": coverage_stats["coverage_fraction"],
        "top_20_uncovered_words": coverage_stats["top_20_uncovered_words"],
        "verdict": "too little coverage to matter" if stats["total_hits"] == 0 else "coverage detected",
        "limits": [
            "Deterministic stemming approximation (final vowel stripping, consonant gradations kk→k, "
            "pp→p, tt→t, nt→nn, nk→ng, k→∅, t→d, p→v, and compound term inflection on last component). "
            "It is not full morphology: rare gradations, stem vowel shifts, internal compound inflections, "
            "and derivational morphology remain unhandled.",
            "Measured over the 29 recorded narration sentences only, and not wired into the narration "
            "evaluation runner; it is a prototype measurement, not a gate.",
            "The publisher's discouraged/deprecated classes are administrative and IT-service vocabulary, "
            "which is the substantive reason the coverage cannot constrain clinical narration.",
        ],
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote results artifact to {output_path}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Finnish narration vocabulary against THL Sote-sanastot.")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH, help="Path to Sote-sanastot snapshot JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output path for results artifact")
    parser.add_argument("--all-adjudicated", action="store_true", help="Check all 35 adjudicated sentences instead of canonical 29")
    args = parser.parse_args()

    result = run_check(
        snapshot_path=args.snapshot,
        output_path=args.output,
        only_canonical_29=not args.all_adjudicated,
    )

    if result.get("status") == "snapshot_absent":
        print(result["message"])
        return 0

    print("=== Narration Vocabulary Control Check ===")
    print(f"Terms checked: {result['terms_checked']} ({result['distinct_terms_checked']} distinct)")
    print(f"Concepts covered: {result['concepts_covered']}")
    print(f"Sentences in scope: {result['sentences_in_scope']}")
    print(f"Sentences with hit: {result['sentences_with_hit']}")
    print(f"Sentences not covered: {result['sentences_not_covered']}")
    print(f"Hits per sentence: {result['hits_per_sentence']}")
    print(f"Total hits: {result['total_hits']}")
    print(f"Narration content words: {result['narration_content_words']}")
    print(f"Covered by vocabulary: {result['covered_by_vocabulary']}")
    print(f"Coverage fraction: {result['coverage_fraction']}")
    print(f"Top 20 uncovered words: {result['top_20_uncovered_words']}")
    print(f"Verdict: {result['verdict']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
