#!/usr/bin/env python
"""Capture the current Container App deployment state from read-only Azure CLI readbacks."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# pi-lens-ignore: reportMissingImports
from app import decision_protocol  # noqa: E402

# The approved presenter identities, as the runbook and `azure/hosting/enable-ingress.sh` state them. Kept
# here rather than imported from the template: a capture that reads its expectation from the artefact it is
# checking records whatever it finds.
APPROVED_PRESENTERS = frozenset({
    "c5fce656-6be6-4248-888d-50d12d837bae",
    "481c90a1-ae87-488f-81df-8ff7074f1616",
    "3acb4344-427a-43f8-8819-cfe3a4e8e758",
    "946a13e4-13a4-450d-98a3-d4621d75057b",
})

# Four coordinates, and nothing about what is deployed. This replaced a tracked hosting parameters file
# that pinned a stale image and buildRevision; see the runbook section "The hosting parameters file is
# deleted, deliberately".
COORDINATES = ROOT / ".scratch/hackathon-readiness/results/deployment-coordinates.json"
OUTPUT = ROOT / ".scratch/hackathon-readiness/results/deployment-state.json"
DIGEST = re.compile(r"@(?P<digest>sha256:[0-9a-f]{64})$")


def _value(coordinates: dict, name: str):
    try:
        return coordinates[name]
    except (KeyError, TypeError) as error:
        raise SystemExit(f"missing deployment coordinate {name}") from error


def _az_optional(*args: str):
    """Read something that may legitimately be absent, without failing the capture."""
    result = subprocess.run(["az", *args, "--output", "json"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _az_json(*args: str) -> dict:
    result = subprocess.run(["az", *args, "--output", "json"], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"az {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"az {' '.join(args)} returned invalid JSON") from error


def main() -> None:
    try:
        coordinates = json.loads(COORDINATES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read the deployment coordinates: {type(error).__name__}") from error
    app_name = _value(coordinates, "appName")
    subscription = _value(coordinates, "subscriptionId")
    resource_group = _value(coordinates, "resourceGroup")
    app = _az_json("containerapp", "show", "--name", app_name, "--resource-group", resource_group,
                   "--subscription", subscription)
    app_properties = app.get("properties") or {}
    revision_name = app_properties.get("latestRevisionName")
    if not revision_name:
        raise SystemExit("containerapp readback has no latestRevisionName")
    revision = _az_json("containerapp", "revision", "show", "--name", app_name,
                        "--resource-group", resource_group, "--revision", revision_name,
                        "--subscription", subscription)
    properties = revision.get("properties") or {}
    template = properties.get("template") or {}
    scale = template.get("scale") or {}
    containers = template.get("containers") or []
    if not containers:
        raise SystemExit("revision readback has no container")
    container = containers[0]
    image = container.get("image") or ""
    digest_match = DIGEST.search(image)
    if not digest_match:
        raise SystemExit("live image reference is not pinned to an unambiguous sha256 digest")
    environment = {item.get("name"): item.get("value") for item in container.get("env", [])}
    traffic = (app_properties.get("configuration") or {}).get("ingress", {}).get("traffic") or []
    traffic_weight = next((item.get("weight") for item in traffic
                           if item.get("latestRevision") or item.get("revisionName") == revision_name), None)
    # The posture the documents assert and the capture could not see. `LUVN_ACCESS_POLICY` is what narrows a
    # caller to particular encounters at all: unset means every ingress-approved presenter reaches every
    # encounter in the corpus, which is the posture this synthetic demo runs under and which the plan records
    # as accepted. The policy BODY is never captured -- an assignment rule names people -- only whether one is
    # configured and a digest of it, so the claim "unconfigured" is checkable instead of asserted.
    policy_text = (environment.get("LUVN_ACCESS_POLICY") or "").strip()
    policy_file = (environment.get("LUVN_ACCESS_POLICY_FILE") or "").strip()
    policy_configured = bool(policy_text or policy_file)
    state = {
        "revision": revision_name,
        "image": image,
        "image_digest": digest_match.group("digest"),
        "provisioning_state": properties.get("provisioningState"),
        "health_state": properties.get("healthState"),
        "running_state": properties.get("runningState"),
        "traffic_weight": traffic_weight,
        "revision_created_at": properties.get("createdTime"),
        "source_commit": environment.get("LUVN_BUILD_REVISION"),
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "foundry_model": environment.get("LUNA_FOUNDRY_MODEL"),
        "foundry_agent_name": environment.get("LUNA_FOUNDRY_AGENT_NAME"),
        "foundry_agent_version": environment.get("LUNA_FOUNDRY_AGENT_VERSION"),
        # The database the app actually reads, so the cutover cannot be asserted without evidence: the app's
        # served corpus is larger than the pre-cutover one, and this is the name it comes from.
        "sql_database": environment.get("LUVN_SQL_DATABASE"),
        "min_replicas": scale.get("minReplicas"),
        "max_replicas": scale.get("maxReplicas"),
    }
    # The configuration the gate used to be blind to. It read fifteen fields, all off the Container App
    # revision, so deleting the authentication policy or opening the ingress would not have failed anything.
    # These are captured now, and the load-bearing ones are required to be present: a capture that cannot see
    # the auth policy must fail loudly rather than record a deployment it did not verify.
    ingress = (app_properties.get("configuration") or {}).get("ingress") or {}
    resource_id = app.get("id")
    auth = _az_optional("rest", "--method", "get", "--url",
                        f"https://management.azure.com{resource_id}/authConfigs/current"
                        f"?api-version=2024-03-01") if resource_id else None
    auth_props = (auth or {}).get("properties") or {}
    identity_providers = auth_props.get("identityProviders") or {}
    aad_validation = ((identity_providers.get("azureActiveDirectory") or {}).get("validation") or {})
    principals = (((aad_validation.get("defaultAuthorizationPolicy") or {})
                   .get("allowedPrincipals") or {}).get("identities")) or []
    # The classifier's deployment set, so a deployment added or left behind is visible.
    account = re.match(r"https://([^.]+)\.", environment.get("LUNA_FOUNDRY_BASE") or "")
    foundry = _az_optional("cognitiveservices", "account", "deployment", "list",
                           "-n", account.group(1), "-g", resource_group) if account else None
    # `unauthenticatedClientAction` lives under `globalValidation`, not at the top of the authConfig.
    # Reading it from the wrong level recorded `null` for the one field that says an anonymous request is
    # redirected rather than served — the field the runbook's ingress gate reads, captured as absent.
    global_validation = auth_props.get("globalValidation") or {}
    allowed = set(principals)
    approved = set(APPROVED_PRESENTERS)
    secret_env_names = sorted(item["name"] for item in container.get("env", []) if item.get("secretRef"))
    state["configuration"] = {
        "ingress": {"external": ingress.get("external"), "allow_insecure": ingress.get("allowInsecure"),
                    "ip_security_restrictions": len(ingress.get("ipSecurityRestrictions") or []),
                    "fqdn": ingress.get("fqdn")},
        "authentication": {"platform_enabled": (auth_props.get("platform") or {}).get("enabled"),
                           "unauthenticated_action": global_validation.get("unauthenticatedClientAction"),
                           "redirect_to_provider": global_validation.get("redirectToProvider"),
                           "entra_enabled": (identity_providers.get("azureActiveDirectory") or {}).get("enabled"),
                           "allowed_audiences": aad_validation.get("allowedAudiences") or [],
                           "allowed_principal_count": len(principals),
                           # The set, not only its size: a capture that recorded "4" would pass while one
                           # approved presenter had been swapped for someone else.
                           "allowed_principals_match_approved": allowed == approved,
                           "provider": "azureActiveDirectory" if identity_providers else None},
        "secret_env_names": secret_env_names,
        # See the note where `policy_configured` is computed: presence and digest, never the policy body.
        "access_policy": {
            "configured": policy_configured,
            "sha256": hashlib.sha256(policy_text.encode()).hexdigest() if policy_text else None,
            "reach": ("the caller's own scope only" if policy_configured
                      else "every encounter, for every ingress-approved presenter"),
        },
        # The decision model is one typed provider reached through the Foundry key (ADR-0018/ADR-0021);
        # `LUVN_DECISION_DEPLOYMENT` names the deployment and the code owns the route. This used to record
        # `LUVN_DECISION_PROVIDER`/`LUVN_VERIFIER_PROVIDER`/`JEV_KEY`, none of which any code reads any more,
        # so the capture reported a vendor as present because a dead environment variable said so.
        "decision_model": {
            "provider": "microsoft-decision-1",
            # A `secretRef` entry carries no `value`, so the plain environment map cannot see it: the
            # key is configured when it is either a literal or a secret reference.
            "configured": bool(environment.get("LUNA_FOUNDRY_KEY")
                               or environment.get("LUNA_FOUNDRY_KEY_IS_SECRET")
                               or "LUNA_FOUNDRY_KEY" in secret_env_names),
            "deployment_env": environment.get("LUVN_DECISION_DEPLOYMENT") or "Decision-1 (default)",
            "protocol_sha256": decision_protocol.SIGNATURE_SHA256,
        },
        "foundry_deployments": sorted(item["name"] for item in foundry) if isinstance(foundry, list) else None,
    }
    for required in ("ingress", "authentication"):
        block = state["configuration"][required]
        if not any(value not in (None, "", [], 0) for value in block.values()):
            raise SystemExit(f"capture cannot see the {required} configuration: refusing to record an "
                             f"unverified deployment")
    auth = state["configuration"]["authentication"]
    if auth["platform_enabled"] is not True:
        raise SystemExit("platform authentication is not enabled: refusing to record this as the deployment")
    if auth["unauthenticated_action"] != "RedirectToLoginPage":
        raise SystemExit(
            f"the unauthenticated action is {auth['unauthenticated_action']!r}, not RedirectToLoginPage: "
            "an anonymous request may be served rather than redirected, so this is not the recorded deployment")
    if auth["allowed_principals_match_approved"] is not True:
        raise SystemExit("the allowed principal set is not the approved presenter set: refusing to record it")

    missing = [key for key, value in state.items() if value is None or value == ""]
    if missing:
        raise SystemExit("live readback missing fields: " + ", ".join(missing))
    OUTPUT.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"captured {revision_name} ({state['min_replicas']}–{state['max_replicas']} replicas)")


if __name__ == "__main__":
    main()
