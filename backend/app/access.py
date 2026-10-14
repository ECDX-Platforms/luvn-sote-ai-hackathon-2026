"""Per-actor authorization for the read routes: which encounters a principal may see.

Authentication in this product is the Container Apps ingress (Entra Easy Auth) and nothing else; the
application has never authenticated anybody. The principal headers therefore name *who* is asking, and
until now that name only attributed audit decisions — it never constrained what was visible, so any
authenticated tenant principal could read every encounter, every lens and every person in the catalogue.
This module is the mechanism that constrains it, supplied by deployment configuration rather than
hardcoded, and deliberately opt-in:

* **No policy configured** (`LUVN_ACCESS_POLICY` and `LUVN_ACCESS_POLICY_FILE` both absent or empty) is
  today's behaviour, exactly: authentication at the ingress is the boundary, and any authenticated
  presenter may read any encounter. The demo works out of the box.
* **A policy configured** fails closed. A principal that matches no rule gets no encounters at all; an
  unknown caller never falls back to open access. An encounter outside the caller's resolved scope is
  refused by the routes on the route's canonical path — 403, with the *same* body whether or not the
  encounter exists, so a caller cannot tell the two states apart (see `main.require_encounter` for why that
  pair is one body and why a non-canonical spelling of the path is a plain unknown-API 404 instead).
* A policy that cannot be parsed fails closed too: it raises `AccessPolicyError`, the app turns that into
  a 500 naming the defect, and nothing is served. It must never silently fall back to open access.

The honest axis is the encounter, not the professional "role". The four roles in this product are
presentation lenses over the same data, not identities — several of them show the *same* person and the
same situation to different professionals — so there is no role-based authority to model. A rule scopes
principals to encounters; nothing here consults an encounter's role.

Configuration contract
----------------------

`LUVN_ACCESS_POLICY` — the policy as inline JSON. `LUVN_ACCESS_POLICY_FILE` — a path to a mounted file
holding the same JSON. Setting both is ambiguous and is refused. An empty string means "not set".

The document is an object with an ordered `rules` list; the first rule that names the caller decides. Its
only required key is `rules`: `version` defaults to the one version this module implements (see
`SUPPORTED_VERSION`), which is the same treatment every other optional key here gets, so `{"rules": [...]}`
means v1 and an operator who wrote no version said nothing rather than something ambiguous.

    {
      "version": 1,
      "rules": [
        {"description": "the medication nurse's own two patients",
         "objects": ["6c0f0a72-0d61-4a0f-9c1a-9f3d2b7e41aa"],
         "upns": ["nurse@example.fi"],
         "encounters": ["bound-encounter-002-laakitys-sairaanhoitaja",
                        "bound-encounter-005-laakitys-sairaanhoitaja"]},
        {"description": "the mental-health team's shared caseload",
         "upns": ["mental-health@example.fi"],
         "encounter_prefixes": ["bound-encounter-003-"]},
        {"description": "an auditor reads one service domain",
         "objects": ["0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"],
         "encounter_suffixes": ["-sosiaalityontekija"]},
        {"description": "revoked — reaches this principal's rule, so matching stops here",
         "upns": ["former@example.fi"], "action": "deny"}
      ]
    }

Per rule:

* `objects` (alias `object_ids`) — Entra object ids; `upns` — user principal names. A caller matches when
  **either** names them, because the ingress supplies both and an operator will know the email. Wildcards
  are not allowed on either: an all-principals rule would be an open policy that defeats the point.
* `encounters` (exact ids), `encounter_prefixes`, or `encounter_suffixes` — exactly one of the three, the
  encounter scope this rule grants. Not a pattern language: a prefix or a suffix is readable in a review
  in a way a glob is not, and `*` is never needed to say "all".
* `action` — `"allow"` (default) or `"deny"`. A deny rule stops matching, so an operator can revoke a
  principal in front of a broader rule without renumbering anything. An unmatched caller is denied anyway.
* `description` — optional, for the operator reading the policy back. Unknown keys are refused rather than
  ignored, so a typo in a key name cannot silently narrow or widen a scope.

An allow rule must carry both a principal and an encounter selector. Two rules selecting exactly the same
principals are refused as ambiguous: "first match wins" cannot be the answer when the match key is
identical.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, NoReturn

# The header names are the platform's; `actor.actor_from_headers` owns reading them.
from .actor import Actor

POLICY_ENV = "LUVN_ACCESS_POLICY"
POLICY_FILE_ENV = "LUVN_ACCESS_POLICY_FILE"

# Encounter ids are opaque operator-supplied strings. This is the syntax they already use, and it is
# checked so a typo in a scope is refused at parse time instead of silently matching nothing.
_ENCOUNTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_ENCOUNTER_FRAGMENT = re.compile(r"^[A-Za-z0-9._-]{1,200}$")

_POLICY_KEYS = frozenset({"version", "rules", "description"})
_RULE_KEYS = frozenset({
    "description", "action",
    "objects", "object_ids", "upns",
    "encounters", "encounter_prefixes", "encounter_suffixes",
})
MAX_POLICY_BYTES = 1_000_000

# The only policy schema version this module implements. `version` is not decorative: a document that
# declares a version this code does not implement is refused at parse time, because parsing a future
# document under today's semantics is how "the schema says v2" and "what the process enforced" drift apart.
SUPPORTED_VERSION = 1


class AccessPolicyError(ValueError):
    """The configured access policy is unusable.

    Raised at parse time and never caught into an open fallback: a malformed policy is a deployment
    defect, and the only safe failure is to serve nothing. `main.py` maps it to a 500 that names it.

    `str(exc)` is the operator's message and may carry configuration the deployment would rather not
    publish — a mounted file path, an encounter id or a UPN read out of the policy — so it belongs in the
    server log. `public_message()` is the same refusal with the path and the raw values withheld, and it is
    what the HTTP response body carries; the two are deliberately different renderings of one fault, not
    two faults.
    """

    def __init__(self, message: str, *, public: str | None = None,
                 prefix: str | None = None, public_prefix: str | None = None) -> None:
        rendered = message if public is None else public
        if prefix is not None:
            message = prefix + message
        if public_prefix is not None:
            rendered = public_prefix + rendered
        super().__init__(message)
        self.public = rendered

    def public_message(self) -> str:
        """The refusal as the wire may carry it: the location and the kind, never a value or a path."""
        return self.public


def _refuse(message: str, public: str) -> NoReturn:
    """Raise `AccessPolicyError` whose log detail is `message` and whose wire detail is `public`.

    The two differ wherever the full message quotes the operator's own value: the response must stay
    actionable to an operator watching for a whole deployment being down, without handing a caller probing
    a misconfigured instance a valid encounter id, a UPN or the mount layout.
    """
    raise AccessPolicyError(message, public=public)


@dataclass(frozen=True)
class EncounterScope:
    """What a principal may read, as resolved from the one rule that matched them.

    A prefix or suffix rule keeps its patterns here rather than being expanded into an id set: the
    catalogue is a live, imported thing — a SQL/BLOB store can hold more encounters than the process was
    built with — and expanding a pattern against a fixture list would silently deny the imported ones.
    `contains` therefore always tests the exact ids *and* the patterns. `grants_all` is true only for a
    scope with no selector at all, which is the fail-closed value an unnamed principal resolves to; the
    parse-time invariant is that it is true exactly when there is nothing to match. It is a separate field
    from the selectors because "matches nothing" and "matches everything" must not be one sentinel.
    """

    encounter_ids: frozenset[str] = frozenset()
    grants_all: bool = False
    encounter_prefixes: tuple[str, ...] = ()
    encounter_suffixes: tuple[str, ...] = ()
    rule: str = ""
    principal_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        has_selector = bool(self.encounter_ids or self.encounter_prefixes or self.encounter_suffixes)
        if self.grants_all and has_selector:
            raise ValueError(
                "an EncounterScope with a selector cannot also grant every encounter; the two are "
                "different answers and a scope must not be both")

    def contains(self, encounter_id: str) -> bool:
        if encounter_id in self.encounter_ids:
            return True
        if any(encounter_id.startswith(prefix) for prefix in self.encounter_prefixes):
            return True
        if any(encounter_id.endswith(suffix) for suffix in self.encounter_suffixes):
            return True
        return self.grants_all


@dataclass(frozen=True)
class PolicyRule:
    objects: frozenset[str]
    upns: frozenset[str]
    action: str
    description: str
    encounter_ids: frozenset[str] = frozenset()
    encounter_prefixes: tuple[str, ...] = ()
    encounter_suffixes: tuple[str, ...] = ()

    def selector_key(self) -> tuple[frozenset[str], frozenset[str]]:
        return self.objects, self.upns


@dataclass(frozen=True)
class AccessPolicy:
    rules: tuple[PolicyRule, ...]


def object_key(value: str) -> str:
    """The comparison key for an object id.

    Entra object ids are GUIDs and case-sensitivity is not part of their identity, and the same value may
    arrive with stray whitespace from a hand-written header. Folding here means one rule can be written
    the way an operator reads it out of the portal.
    """
    return value.strip().casefold()


def upn_key(value: str) -> str:
    """The comparison key for a user principal name, for the same reasons as `object_key`."""
    return value.strip().casefold()


def principal_keys(actor: Actor | None) -> frozenset[str]:
    """Every configured-principal key this actor can be named by: the object id and/or the UPN.

    An actor with no principal at all has no keys and therefore matches nothing — the fail-closed case.
    Both keys are carried, not compared to each other: a rule naming the object id matches a caller the
    ingress also gave an email for, and a rule naming the email matches that same caller.
    """
    if actor is None:
        return frozenset()
    keys = []
    if actor.actor_id and actor.actor_id.strip():
        keys.append(f"oid:{object_key(actor.actor_id)}")
    if actor.name and actor.name.strip():
        keys.append(f"upn:{upn_key(actor.name)}")
    return frozenset(keys)


def _require_string_list(raw: Any, where: str) -> list[str]:
    if not isinstance(raw, list):
        raise AccessPolicyError(f"{where} must be a list of strings")
    values: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise AccessPolicyError(f"{where}[{index}] must be a string")
        stripped = value.strip()
        if not stripped:
            raise AccessPolicyError(f"{where}[{index}] is empty")
        if any(character.isspace() for character in stripped):
            # This is the parser's only check that can fire on a *plausible* encounter id (a pasted id with a
            # trailing space), so the value is withheld from the wire: echoing it would put a real id — the
            # exact thing a probe wants — into a 500 body. The log keeps it, because an operator diagnosing
            # the paste needs to see the space, and the position says which entry to fix either way.
            _refuse(f"{where}[{index}] must not contain whitespace: {stripped!r}",
                    f"{where}[{index}] must not contain whitespace")
        values.append(stripped)
    if not values:
        raise AccessPolicyError(f"{where} must not be empty")
    return values


def _parse_encounter_patterns(raw: Any, where: str) -> list[str]:
    values = _require_string_list(raw, where)
    for value in values:
        # A prefix/suffix is a literal fragment of an id, not a glob: no wildcard, no separator games.
        if "*" in value or "?" in value or "/" in value:
            _refuse(
                f"{where} value {value!r} must be a literal id fragment (no '*', '?' or '/')",
                f"{where} must contain literal id fragments only; no '*', '?' or '/'")
        if not _ENCOUNTER_FRAGMENT.fullmatch(value):
            _refuse(
                f"{where} value {value!r} is not an encounter id fragment",
                f"{where} contains a value that is not an encounter id fragment")
    return values


def _parse_encounter_ids(raw: Any, where: str) -> list[str]:
    values = _require_string_list(raw, where)
    for value in values:
        if "*" in value or "?" in value:
            _refuse(
                f"{where} entry {value!r} must be an exact encounter id; use `encounter_prefixes` or "
                "`encounter_suffixes` to scope by pattern",
                f"{where} must contain exact encounter ids; use `encounter_prefixes` or "
                "`encounter_suffixes` to scope by pattern")
        if not _ENCOUNTER_ID.fullmatch(value):
            _refuse(
                f"{where} entry {value!r} is not a valid encounter id",
                f"{where} contains an entry that is not a valid encounter id")
    return values


def _rule_from_json(raw: Any, index: int,
                    seen: dict[tuple[frozenset[str], frozenset[str]], int]) -> PolicyRule:
    where = f"rules[{index}]"
    if not isinstance(raw, dict):
        raise AccessPolicyError(f"{where} must be an object")
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise AccessPolicyError(
            f"{where} has unknown key(s) {sorted(unknown)}; known keys are {sorted(_RULE_KEYS)}")
    if "objects" in raw and "object_ids" in raw:
        raise AccessPolicyError(f"{where} sets both 'objects' and 'object_ids'; use one")

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise AccessPolicyError(f"{where}.description must be a string")

    action = raw.get("action", "allow")
    if action not in ("allow", "deny"):
        # Note `action` is echoed to neither rendering: "must be 'allow' or 'deny'" is the whole fact, and
        # the only values that could stand here are the two the operator should have written.
        raise AccessPolicyError(f"{where}.action must be 'allow' or 'deny'")

    raw_objects = raw.get("objects", raw.get("object_ids"))
    objects = _require_string_list(raw_objects, f"{where}.objects") if raw_objects is not None else []
    raw_upns = raw.get("upns")
    upns = _require_string_list(raw_upns, f"{where}.upns") if raw_upns is not None else []
    if not objects and not upns:
        raise AccessPolicyError(
            f"{where} names no principal; at least one of 'objects' or 'upns' is required, and a rule "
            "without one would never match anybody")

    selectors = [name for name in ("encounters", "encounter_prefixes", "encounter_suffixes")
                 if raw.get(name) is not None]
    if len(selectors) > 1:
        raise AccessPolicyError(
            f"{where} sets {selectors}; give exactly one encounter selector")
    encounter_ids: list[str] = []
    prefixes: list[str] = []
    suffixes: list[str] = []
    if "encounters" in selectors:
        encounter_ids = _parse_encounter_ids(raw["encounters"], f"{where}.encounters")
    elif "encounter_prefixes" in selectors:
        prefixes = _parse_encounter_patterns(raw["encounter_prefixes"], f"{where}.encounter_prefixes")
    elif "encounter_suffixes" in selectors:
        suffixes = _parse_encounter_patterns(raw["encounter_suffixes"], f"{where}.encounter_suffixes")
    if action == "allow" and not selectors:
        raise AccessPolicyError(
            f"{where} is an allow rule with no encounter scope; give exactly one of 'encounters', "
            "'encounter_prefixes' or 'encounter_suffixes' (an allow rule that scopes nothing would grant "
            "every encounter and turn the configured policy back into open access)")

    rule = PolicyRule(
        objects=frozenset(object_key(value) for value in objects),
        upns=frozenset(upn_key(value) for value in upns),
        action=action,
        description=description,
        encounter_ids=frozenset(encounter_ids),
        encounter_prefixes=tuple(prefixes),
        encounter_suffixes=tuple(suffixes),
    )
    # Two rules with the same principal selector make "first match wins" meaningless: the operator cannot
    # tell which one applies, and the answer would change with list order for no visible reason.
    if rule.selector_key() in seen:
        raise AccessPolicyError(
            f"{where} selects the same principal(s) as rules[{seen[rule.selector_key()]}]; merge them or "
            "make the selectors distinct")
    seen[rule.selector_key()] = index
    return rule


def parse_policy(raw: str) -> AccessPolicy:
    """Parse and validate policy JSON, or raise `AccessPolicyError` naming what is wrong.

    Documented shape is enforced shape: `version` is optional and absent means `SUPPORTED_VERSION`, the
    version this process actually implements, exactly as an absent `action` means `"allow"` and an absent
    `description` means `""`. A version that is present and is not that one is refused, because nothing
    else can be honoured: this parser knows only v1 semantics, so accepting `2` would apply v1 rules to a
    document that asked for something else and the operator would have no signal that their schema was
    ignored. Refusing here — the same `AccessPolicyError` and the same fail-closed 500 as every other
    malformed policy — is the loud, safe answer.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise AccessPolicyError("the policy document is empty")
    if len(raw.encode("utf-8")) > MAX_POLICY_BYTES:
        raise AccessPolicyError(f"the policy document exceeds {MAX_POLICY_BYTES} bytes")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AccessPolicyError(f"the policy is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise AccessPolicyError(
            "the policy must be a JSON object with a 'rules' list, not a "
            f"{type(document).__name__}")
    unknown = set(document) - _POLICY_KEYS
    if unknown:
        raise AccessPolicyError(
            f"the policy has unknown top-level key(s) {sorted(unknown)}; known keys are "
            f"{sorted(_POLICY_KEYS)}")
    # Checked after the key set and before the rules: an unknown key is a different defect in the same
    # document, and this ordering keeps that refusal — which names no value — the one such a document gets.
    version = document.get("version", SUPPORTED_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != SUPPORTED_VERSION:
        # The declared version is the operator's own schema claim, not store data, so the log names it; the
        # response names the *property* instead, because a version key can hold any JSON value and echoing
        # an arbitrary one is exactly the habit this handler is being cured of.
        _refuse(
            f"the policy declares version {version!r}, and this process implements version "
            f"{SUPPORTED_VERSION} only; publish a policy this module can honour, or omit 'version' "
            f"(which means {SUPPORTED_VERSION})",
            f"the policy's 'version' is not the version this process implements "
            f"({SUPPORTED_VERSION}); omit 'version' to mean {SUPPORTED_VERSION}")
    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        raise AccessPolicyError(
            "the policy must carry a non-empty 'rules' list; a policy with no rules would deny everybody, "
            "which is a configuration mistake rather than something to guess at")
    seen: dict[tuple[frozenset[str], frozenset[str]], int] = {}
    return AccessPolicy(rules=tuple(_rule_from_json(rule, index, seen)
                                    for index, rule in enumerate(rules)))


def policy_source_from_env(environ: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """`(kind, value)` for the configured policy, or `None` when none is configured.

    Empty is absent: Container Apps often materialises an unset variable as `""`, and treating that as an
    unusable policy would break the demo in exactly the deployment most likely to be misconfigured.
    """
    environment = os.environ if environ is None else environ
    inline = (environment.get(POLICY_ENV) or "").strip()
    from_file = (environment.get(POLICY_FILE_ENV) or "").strip()
    if inline and from_file:
        raise AccessPolicyError(
            f"both {POLICY_ENV} and {POLICY_FILE_ENV} are set; the policy is ambiguous. Set exactly one.")
    if inline:
        return ("inline", inline)
    if from_file:
        return ("file", from_file)
    return None


@lru_cache(maxsize=16)
def _read_policy_file(path: str) -> str:
    """Read the mounted policy file, once per path.

    Cached so the file is read at first use rather than on every request; the process-level answer to
    "what is the policy" does not change while the process runs, which is the same guarantee the inline
    variable gives. A failure is deliberately not cached: a transient mount problem should be retried,
    and it still fails closed on the request that hit it.

    Neither rendering names the path. The operator's log line already reaches an operator, who configured
    the path in the first place and can read it out of the deployment; the *response* reaches whoever sent
    the request, and a caller probing a misconfigured instance must not learn the mount layout from it. The
    variable name and the errno kind survive, so an operator still sees "the file variable is unusable"
    without a shell on the box.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        _refuse(
            f"{POLICY_FILE_ENV} could not be read at {path!r}: {exc}",
            f"{POLICY_FILE_ENV} names a file this process could not read ({type(exc).__name__})")


def policy_from_env(environ: Mapping[str, str] | None = None) -> AccessPolicy | None:
    """The configured policy, or `None` when the deployment has configured none.

    Re-read per request on purpose: it is a handful of environment lookups plus (once) a file read, it
    keeps "change the policy" a restart-and-nothing-else operation, and it lets a test set the variable for
    the exact request it is exercising instead of reaching into module state.

    A file-sourced parse fault is annotated with the path — and with the variable, in both renderings, since
    the variable name is what an operator acts on and discloses nothing. The log gets
    `LUVN_ACCESS_POLICY_FILE (<path>): <fault>`, the response gets `LUVN_ACCESS_POLICY_FILE: <fault>`; that is
    why the wrapper carries two prefixes rather than rebuilding one message from `str(exc)`.
    """
    source = policy_source_from_env(environ)
    if source is None:
        return None
    kind, value = source
    raw = _read_policy_file(value) if kind == "file" else value
    try:
        return parse_policy(raw)
    except AccessPolicyError as exc:
        if kind == "file":
            raise AccessPolicyError(str(exc), public=exc.public_message(),
                                    prefix=f"{POLICY_FILE_ENV} ({value}): ",
                                    public_prefix=f"{POLICY_FILE_ENV}: ") from exc
        raise


def resolve_scope(policy: AccessPolicy, actor: Actor | None) -> EncounterScope | None:
    """The scope for this actor under this policy, or `None` when the actor may read nothing.

    First matching rule wins, and both actions are a result: an `allow` yields its encounter scope, a
    `deny` yields `None` and stops matching. A policy with no rule for this principal yields `None` too —
    an unknown caller gets nothing, never the open fallback that an unset policy gives.
    """
    keys = principal_keys(actor)
    if not keys:
        return None
    for rule in policy.rules:
        named = {f"oid:{key}" for key in rule.objects} | {f"upn:{key}" for key in rule.upns}
        if not keys & named:
            continue
        if rule.action == "deny":
            return None
        return EncounterScope(
            encounter_ids=rule.encounter_ids,
            encounter_prefixes=rule.encounter_prefixes,
            encounter_suffixes=rule.encounter_suffixes,
            rule=rule.description,
            principal_keys=keys,
        )
    return None


def scope_for_actor(actor: Actor | None, environ: Mapping[str, str] | None = None) -> EncounterScope | None:
    """The scope for this actor, in the two `None`s the routes must tell apart.

    `None` means the deployment configured no policy and the caller may read every encounter — the open
    demo behaviour. A *configured* policy never returns `None`: a principal it does not name, or a deny
    rule, resolves to an empty `EncounterScope`, which reads nothing and refuses every scoped route. The
    empty scope is a value rather than a second `None` so that a route cannot confuse "open" with "denied"
    by forgetting which sentinel it received.
    """
    policy = policy_from_env(environ)
    if policy is None:
        return None
    return resolve_scope(policy, actor) or EncounterScope()


def may_read(actor: Actor | None, encounter_id: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether this actor may read this encounter. Open when no policy is configured."""
    scope = scope_for_actor(actor, environ)
    return scope is None or scope.contains(encounter_id)
