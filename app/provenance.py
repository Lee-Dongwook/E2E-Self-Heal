"""Non-sensitive provenance for the evidence that left the machine.

The ``--json`` result records which provider, model, and endpoint saw the repair evidence, and
whether the evidence boundary replaced anything before sending it. Nothing here is derived from
prompt text or a provider response, and the endpoint is redacted before it is reported.
"""

import ipaddress
from typing import cast
from urllib.parse import urlsplit

from app.config import settings
from app.llm import resolved_endpoint
from app.schemas import ProviderProvenance
from app.shadow.redaction import redact_url
from app.state import AgentState

# A hostless endpoint cannot leave the machine, and the loopback names never resolve to a
# remote peer in practice.
_LOCAL_HOSTNAMES = frozenset({"", "localhost", "localhost.localdomain", "::1"})


def is_local_endpoint(endpoint: str) -> bool:
    """Whether ``endpoint`` addresses this machine.

    Derived from the address only: a loopback or a private/link-local literal is local, anything
    else is remote. No DNS lookup is performed, so a hostname that resolves to a private address
    is reported as remote, the conservative answer for a data boundary.
    """
    host = urlsplit(endpoint).hostname or ""
    if host.casefold() in _LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def provider_provenance(state: AgentState) -> ProviderProvenance:
    """Describe where this run sent evidence, from configuration and graph state."""
    endpoint = resolved_endpoint(settings.llm_provider)
    count = state.get("redaction_count", 0)
    return ProviderProvenance(
        provider=settings.llm_provider,
        model=settings.llm_model,
        endpoint=cast(str, redact_url(endpoint)),
        mode="local" if is_local_endpoint(endpoint) else "remote",
        redaction_applied=bool(state.get("redaction_applied", count > 0)),
        redaction_count=count,
    )
