"""Who is acting.

An audit trail without an actor is not an audit trail, and until now the backend read no principal at all:
Entra authorises at the Container Apps ingress and the application never learned who the professional was, so
"who accepted this suggestion" was unanswerable.

Container Apps Easy Auth injects the authenticated principal's claims as request headers once the platform has
validated the token, so the application can read the actor without implementing authentication itself. The
header names are the platform's, not ours; `X-MS-CLIENT-PRINCIPAL-ID` is the object id and
`X-MS-CLIENT-PRINCIPAL-NAME` the user principal name.

Fail-closed: with no principal there is no actor, and a surface that must record a decision refuses rather
than recording it anonymously. The identity is used for the audit trail and nothing else — it never reaches a
model, and it is never joined to clinical content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

ID_HEADER = "x-ms-client-principal-id"
NAME_HEADER = "x-ms-client-principal-name"
SOURCE = "ingress_principal"


@dataclass(frozen=True)
class Actor:
    """The authenticated professional, as the platform asserts them."""

    actor_id: str
    name: str | None = None
    source: str = SOURCE

    def as_record(self) -> dict:
        return {"actor_id": self.actor_id, "actor_name": self.name, "actor_source": self.source}


def actor_from_headers(headers: Mapping[str, str]) -> Actor | None:
    """Read the platform-asserted principal, or None when the request carries none.

    Header lookups are case-insensitive because HTTP header names are, and the mapping handed in may be a
    plain dict from a test rather than the framework's case-insensitive mapping.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    actor_id = (lowered.get(ID_HEADER) or "").strip()
    name = (lowered.get(NAME_HEADER) or "").strip() or None
    if not actor_id:
        # The platform may assert the principal only in the combined claims header. Presence is enough to
        # know *someone* authenticated; without an id there is no audit key, so it is not an actor.
        return None
    return Actor(actor_id=actor_id, name=name)
