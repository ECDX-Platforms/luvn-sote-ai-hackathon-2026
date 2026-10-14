"""The hosting template may not carry a secret as a literal value.

The live container app ran the App Insights connection string as an **inline** secret until 2026-10-11:
`value: appInsights.properties.ConnectionString` in this template. That is readable by anyone holding
`containerApps/read` — including a reader account that is also an allowed presenter — and the connection
string grants write access to telemetry that can carry record-derived text. The live app was moved to a Key
Vault reference, which leaves the *template* as the place the inline form would come back from: the next
template deploy, or anyone rebuilding the app from `main.bicep`, would put it back.

So this is a source-level assertion rather than a deployment check. It reads the template and refuses any
`secrets[]` entry that carries a literal `value`, and it requires every entry to name a Key Vault URL and an
identity. That is exactly the class of drift the deleted parameters file belonged to: the live app and the
template disagreeing, with the template being the thing a future operator runs.

It also asserts the shape the Container App docs require for the reference to resolve — a `keyVaultUrl` and
an `identity` — and that the three known secret names are all Key Vault references, so a rename cannot
quietly reduce the count.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "azure" / "hosting" / "main.bicep"

# The secrets the container app uses. Named here so a rename shows up as a failure rather than as a smaller
# check that still passes.
REQUIRED_SECRETS = frozenset({
    "entra-client-secret",
    "luna-foundry-key",
    "appinsights-connection-string",
})


def _secrets_block(text: str) -> str:
    """The text of the `secrets:` array in the container-app resource.

    Located by brace matching from the `secrets: [` that introduces an array of objects, rather than by a
    regex over lines: a nested `keyVaultUrl` line with an interpolation in it is not something a line-based
    parse can be trusted to bound.
    """
    match = re.search(r"\bsecrets:\s*\[", text)
    assert match is not None, "the template no longer declares a `secrets:` array at all"
    depth = 0
    start = match.end() - 1
    for index in range(start, len(text)):
        character = text[index]
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError("unbalanced `secrets:` array in the template")


def test_template_has_a_secrets_array_with_the_required_names():
    block = _secrets_block(TEMPLATE.read_text(encoding="utf-8"))
    names = set(re.findall(r"name:\s*([A-Za-z][A-Za-z0-9]*)", block))
    # The names are variables (`authSecretName`, `lunaSecretName`, `appInsightsSecretName`), so resolve them.
    variables = dict(re.findall(r"var\s+(\w+)\s*=\s*'([^']+)'", TEMPLATE.read_text(encoding="utf-8")))
    resolved = {variables.get(name, name) for name in names}
    assert resolved == REQUIRED_SECRETS, (
        f"the template's secrets are {sorted(resolved)}, expected {sorted(REQUIRED_SECRETS)}; "
        "a rename here has to be reflected in this test, and a missing secret means the app has no key"
    )


def _strip_comments(text: str) -> str:
    """Drop `//` comment lines.

    The block deliberately names the inline form in a comment so the next editor knows why it is gone; a
    check that reads the comment would report the very string that documents its absence. Bicep has no
    block comments, so removing whole lines that start with `//` is enough.
    """
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


def test_no_secret_is_a_literal_value():
    block = _strip_comments(_secrets_block(TEMPLATE.read_text(encoding="utf-8")))
    literal = re.findall(r"\bvalue:\s*([^\n]+)", block)
    assert not literal, (
        "a container-app secret carries a literal `value:` — every secret must be a Key Vault reference "
        f"(found {literal}). An inline secret is readable by anyone with `containerApps/read`."
    )


def _entries(block: str) -> list[str]:
    """Split the array into entries by their `name:` line.

    Not by braces: `keyVaultUrl` is an interpolated string, `${keyVault.properties.vaultUri}`, so brace
    matching treats the interpolation as a nested object and reports twice as many entries as there are.
    """
    entries: list[list[str]] = []
    for line in block.splitlines():
        if re.match(r"\s*name:\s*\w+", line):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    return ["\n".join(lines) for lines in entries]


def test_every_secret_is_a_keyvault_reference_with_an_identity():
    block = _strip_comments(_secrets_block(TEMPLATE.read_text(encoding="utf-8")))
    entries = _entries(block)
    assert len(entries) == len(REQUIRED_SECRETS), f"expected three secret entries, found {len(entries)}"
    for entry in entries:
        assert "keyVaultUrl:" in entry, f"a secret entry has no keyVaultUrl: {entry.strip()[:80]}"
        assert "identity:" in entry, (
            f"a secret entry has a keyVaultUrl but no identity, so the platform cannot resolve it: "
            f"{entry.strip()[:80]}"
        )


def test_the_inline_form_is_named_in_a_comment_so_it_is_not_reintroduced_silently():
    """A negative control: the file says why, next to the entry it is about."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "NOT `value:" in text, (
        "the comment explaining why the App Insights secret is a Key Vault reference is gone; without it "
        "the inline form looks like the simpler choice to the next editor"
    )
