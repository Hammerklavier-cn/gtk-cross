"""Dependency resolution: topological sort with cycle detection."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional


def resolve(recipe: Dict[str, List[str]], names: Iterable[str]) -> List[str]:
    """Return the requested names plus all transitive deps, deps-first.

    Raises ValueError on unknown names or dependency cycles.
    """
    order: List[str] = []
    state: Dict[str, int] = {}  # 0=visiting, 1=done
    stack: List[str] = []

    def visit(name: str) -> None:
        st = state.get(name)
        if st == 1:
            return
        if st == 0:
            cycle = " -> ".join(stack[stack.index(name):] + [name])
            raise ValueError(f"dependency cycle detected: {cycle}")
        if name not in recipe:
            raise ValueError(f"unknown recipe {name!r}")
        state[name] = 0
        stack.append(name)
        for dep in recipe[name]:
            visit(dep)
        stack.pop()
        state[name] = 1
        order.append(name)

    for name in names:
        visit(name)
    return order
