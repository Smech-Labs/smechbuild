"""Build-dependency graph, ordering, and rebuild planning (binNMU-style)."""

import re
from dataclasses import dataclass, field

from . import recipe as recipe_mod


class CycleError(Exception):
    def __init__(self, cycle):
        self.cycle = cycle
        super().__init__("build-dependency cycle: " + " -> ".join(cycle))


_ALT = re.compile(r"^\s*([A-Za-z0-9+.\-]+)\s*(?:\(\s*(>=|<=|>>|<<|=)\s*([^)\s]+)\s*\))?\s*$")


def parse_alternatives(expr: str):
    """'a (>= 1) | b' -> [(name, op, ver), ...]; op/ver are '' when absent."""
    out = []
    for alt in expr.split("|"):
        m = _ALT.match(alt)
        if not m:
            raise ValueError(f"cannot parse dependency {expr!r}")
        out.append((m.group(1), m.group(2) or "", m.group(3) or ""))
    return out


def provider_map(recipes):
    """binary or virtual name -> set of source names that produce it."""
    prov = {}
    for r in recipes.values():
        for b in r.packages:
            prov.setdefault(b.name, set()).add(r.name)
            for p in recipe_mod.split_deps(b.provides):
                prov.setdefault(parse_alternatives(p)[0][0], set()).add(r.name)
    return prov


def build_edges(recipes):
    """Return (edges, external): edges[src] = set of sources it build-depends
    on; external[src] = names no recipe provides (come from the base system)."""
    prov = provider_map(recipes)
    edges = {n: set() for n in recipes}
    external = {n: [] for n in recipes}
    for r in recipes.values():
        for expr in r.build_depends:
            alts = parse_alternatives(expr)
            hit = False
            for name, _, _ in alts:
                srcs = prov.get(name, set()) - {r.name}
                if srcs:
                    hit = True
                    edges[r.name] |= srcs
                    break  # first satisfiable alternative wins
            if not hit:
                external[r.name].append(expr)
    return edges, external


def toposort(edges):
    """Dependencies first. Deterministic. Raises CycleError with the cycle."""
    order, state = [], {}

    def visit(n, stack):
        s = state.get(n)
        if s == 2:
            return
        if s == 1:
            i = stack.index(n)
            raise CycleError(stack[i:] + [n])
        state[n] = 1
        for d in sorted(edges[n]):
            visit(d, stack + [n])
        state[n] = 2
        order.append(n)

    for n in sorted(edges):
        visit(n, [])
    return order


def reverse_closure(edges, roots):
    """All sources that (transitively) build-depend on any of roots."""
    rev = {n: set() for n in edges}
    for n, ds in edges.items():
        for d in ds:
            rev[d].add(n)
    seen, todo = set(), list(roots)
    while todo:
        n = todo.pop()
        for m in rev[n]:
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return seen


@dataclass
class Verdict:
    state: str  # up-to-date | needs-build | dep-wait | blocked
    reasons: list = field(default_factory=list)


def plan(recipes, buildinfos, repo, builder_digest=None):
    """Decide what every source needs.

    buildinfos: {source: buildinfo dict of its last successful build}
    repo: {binary name: {"version", "abi", "source"}} of what is published.

    A source is `needs-build` for its own reasons (new version, changed recipe,
    ABI change in something it was built against). It is `dep-wait` while any
    source it build-depends on still has to be (re)built, so nothing is built
    against a library that is about to change.
    """
    edges, _external = build_edges(recipes)
    try:
        order = toposort(edges)
    except CycleError as e:
        return {n: Verdict("blocked", [str(e)]) for n in recipes}

    own = {}
    for n in order:
        r = recipes[n]
        bi = buildinfos.get(n)
        reasons = []
        if bi is None:
            reasons.append("never built")
        else:
            if bi.get("version") != r.full_version:
                reasons.append(f"version {bi.get('version')} -> {r.full_version}")
            if bi.get("recipe_digest") != recipe_mod.digest(r):
                reasons.append("recipe changed")
            if builder_digest and bi.get("builder_digest") != builder_digest:
                reasons.append("build tooling changed")
            for d in bi.get("build_depends", []):
                cur = repo.get(d["name"])
                if cur is None:
                    reasons.append(f"build-dependency {d['name']} is no longer in the repo")
                elif cur.get("abi") != d.get("abi"):
                    reasons.append(f"ABI of {d['name']} changed")
        own[n] = reasons

    verdict = {}
    for n in order:
        pending_deps = sorted(d for d in edges[n] if verdict[d].state != "up-to-date")
        if own[n] and pending_deps:
            verdict[n] = Verdict("dep-wait", own[n] + [f"waiting for {', '.join(pending_deps)}"])
        elif own[n]:
            verdict[n] = Verdict("needs-build", own[n])
        else:
            # Up to date against what is published. If a dependency is about
            # to change, whether this must rebuild is decided after it lands
            # (its new ABI is compared with the buildinfo then).
            verdict[n] = Verdict("up-to-date", [])
    return verdict
