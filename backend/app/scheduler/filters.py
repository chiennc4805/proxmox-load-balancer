from collections.abc import Sequence


def online_node_names(nodes: Sequence[str], excluded_nodes: Sequence[str] = ()) -> list[str]:
    excluded = set(excluded_nodes)
    return sorted({node for node in nodes if node and node not in excluded})