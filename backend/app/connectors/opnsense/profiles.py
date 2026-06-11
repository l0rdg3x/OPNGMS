"""Declarative (edition, version) capability matrix for the OPNsense connector.

Each capability maps to an ordered list of ProfileRule; the resolver returns the EndpointSpec
of the first rule whose (edition, version-range) matches a device. The LAST rule of every
capability MUST be the unconstrained default (edition="any", no bounds).
"""
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    method: str             # "GET" | "POST"
    path: str               # e.g. "diagnostics/traffic/interface" (may include a ?query)
    body: dict | None = None
    kind: str = "json"      # "json" | "text"


@dataclass(frozen=True)
class EndpointSpec:
    requests: tuple          # tuple[Request, ...] — 1 for most capabilities; 4 for system_info
    combine: Callable        # combine(list_of_decoded_responses) -> normalized result


@dataclass(frozen=True)
class ProfileRule:
    edition: str             # "community" | "business" | "devel" | "any"
    min_version: tuple | None  # inclusive lower bound (parse_version tuple) or None
    max_version: tuple | None  # EXCLUSIVE upper bound or None
    spec: EndpointSpec


# The real CAPABILITIES matrix is added in a later task.
CAPABILITIES: dict[str, list[ProfileRule]] = {}
