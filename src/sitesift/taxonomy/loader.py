"""Topic taxonomy: load a neutral YAML into a graph with lookup + validation.

The default is the small in-repo ``taxonomy_custom.yaml``. IAB Content 3.1 can
be shipped as a second vendored YAML and selected via config. The format is
provider-neutral so any taxonomy can be plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import yaml

_BUILTIN = {"sitesift-custom-1": "taxonomy_custom.yaml"}


class TaxonomyError(ValueError):
    """The taxonomy file is missing, unparseable, or internally inconsistent."""


@dataclass
class Node:
    id: str
    name: str
    tier: int
    parent: str | None = None
    aliases: list[str] = field(default_factory=list)
    hints: str | None = None


@dataclass
class Taxonomy:
    id: str
    version: str
    source: str
    nodes: dict[str, Node]
    children: dict[str, list[str]]

    def get(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def tier1(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.tier == 1]

    def children_of(self, node_id: str) -> list[Node]:
        return [self.nodes[c] for c in self.children.get(node_id, [])]

    def is_descendant(self, child_id: str, parent_id: str) -> bool:
        """True if ``child_id`` is (transitively) under ``parent_id``."""
        cur = self.nodes.get(child_id)
        while cur is not None and cur.parent is not None:
            if cur.parent == parent_id:
                return True
            cur = self.nodes.get(cur.parent)
        return False

    def prompt_lines(self, max_tier: int = 2) -> list[str]:
        """Lines for the classifier system prompt: ``id | Tier1 > Tier2``."""
        lines: list[str] = []
        for node in self.nodes.values():
            if node.tier > max_tier:
                continue
            path = self._path_names(node.id)
            line = f"{node.id} | {' > '.join(path)}"
            if node.hints:
                line += f"  (hints: {node.hints})"
            lines.append(line)
        return lines

    def _path_names(self, node_id: str) -> list[str]:
        names: list[str] = []
        cur = self.nodes.get(node_id)
        while cur is not None:
            names.append(cur.name)
            cur = self.nodes.get(cur.parent) if cur.parent else None
        return list(reversed(names))


def load_taxonomy(*, taxonomy_id: str = "sitesift-custom-1", path: str = "") -> Taxonomy:
    """Load a taxonomy by explicit ``path`` or by bundled ``taxonomy_id``."""
    if path:
        raw = Path(path).expanduser().read_text(encoding="utf-8")
    else:
        filename = _BUILTIN.get(taxonomy_id)
        if filename is None:
            raise TaxonomyError(f"unknown taxonomy id {taxonomy_id!r}; known: {sorted(_BUILTIN)}")
        raw = (files("sitesift.taxonomy.data") / filename).read_text(encoding="utf-8")
    return _parse(raw)


def _parse(raw: str) -> Taxonomy:
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or "nodes" not in data:
        raise TaxonomyError("taxonomy must be a mapping with a 'nodes' list")

    nodes: dict[str, Node] = {}
    for entry in data["nodes"]:
        node = Node(
            id=str(entry["id"]),
            name=str(entry["name"]),
            tier=int(entry["tier"]),
            parent=str(entry["parent"]) if entry.get("parent") else None,
            aliases=[str(a) for a in entry.get("aliases", [])],
            hints=str(entry["hints"]) if entry.get("hints") else None,
        )
        if node.id in nodes:
            raise TaxonomyError(f"duplicate node id: {node.id}")
        nodes[node.id] = node

    children: dict[str, list[str]] = {}
    for node in nodes.values():
        if node.parent is not None:
            if node.parent not in nodes:
                raise TaxonomyError(f"node {node.id} has unknown parent {node.parent}")
            if nodes[node.parent].tier != node.tier - 1:
                raise TaxonomyError(
                    f"node {node.id} (tier {node.tier}) parent {node.parent} has wrong tier"
                )
            children.setdefault(node.parent, []).append(node.id)

    return Taxonomy(
        id=str(data.get("id", "unknown")),
        version=str(data.get("version", "0")),
        source=str(data.get("source", "")),
        nodes=nodes,
        children=children,
    )
