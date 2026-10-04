"""Select reachable blob URLs without coupling control and data-plane addresses."""

from __future__ import annotations

import sqlite3


def locations(connection: sqlite3.Connection, digest: str, requester: str | None,
              live_after: float) -> list[str]:
    """Prefer a shared private LAN, retaining public URLs as fallbacks.

    A private address is offered only when both agents registered the same
    nonempty group. Wi-Fi-only agents therefore never receive an unreachable
    Ethernet URL, while a failed Ethernet fetch can still use Wi-Fi.
    """

    group = ""
    if requester:
        row = connection.execute(
            "SELECT private_group FROM nodes WHERE node_name=?", (requester,)
        ).fetchone()
        if row is not None:
            group = row[0]
    sources = connection.execute(
        "SELECT r.location,n.private_address,n.private_group FROM replicas r "
        "JOIN nodes n USING(node_name) WHERE r.artifact_hash=? AND r.verified=1 "
        "AND n.last_heartbeat>? ORDER BY n.node_name",
        (digest, live_after),
    ).fetchall()
    preferred = [f"{row[1].rstrip('/')}/blobs/{digest}" for row in sources
                 if group and row[2] == group and row[1]]
    return preferred + [row[0] for row in sources]
