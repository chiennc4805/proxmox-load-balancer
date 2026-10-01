"""Small Python boundary around the compiled Proxmox TOPSIS extension."""

from collections.abc import Sequence
from typing import Any


def rank_nodes(
    nodes: Sequence[dict[str, Any]], resource: dict[str, Any]
) -> list[dict[str, Any]]:
    try:
        from _rust_topsis import score_nodes
    except ImportError as exc:
        raise RuntimeError("Rust TOPSIS extension is not installed") from exc

    node_inputs = [
        (
            node["name"],
            float(node["cpu"]),
            int(node["maxcpu"]),
            int(node["mem"]),
            int(node["maxmem"]),
        )
        for node in nodes
    ]
    scores = score_nodes(
        node_inputs,
        float(resource["maxcpu"]),
        int(resource["maxmem"]),
    )
    return [
        {"node": node, "score": float(score)}
        for node, score in sorted(scores, key=lambda item: (-item[1], item[0]))
    ]
