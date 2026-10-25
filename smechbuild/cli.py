"""smechbuild command line."""

import argparse
import os
import sys
import time

from . import build as build_mod, buildd, bootstrap, config, graph, recipe, repo, state


def _cfg(args):
    return config.load(args.config)


def cmd_check(args):
    cfg = _cfg(args)
    try:
        recipes = recipe.load_all(cfg.pkgs)
    except recipe.RecipeError as e:
        print(e, file=sys.stderr)
        return 1
    edges, external = graph.build_edges(recipes)
    try:
        order = graph.toposort(edges)
    except graph.CycleError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{len(recipes)} source package(s), build order: {' '.join(order)}")
    for n in order:
        if external[n]:
            print(f"  {n}: from the base system: {', '.join(external[n])}")
    return 0


def cmd_plan(args):
    cfg = _cfg(args)
    recipes = recipe.load_all(cfg.pkgs)
    verdicts = graph.plan(recipes, repo.load_buildinfos(cfg.repo), repo.load_repo_state(cfg.repo),
                          builder_digest=build_mod.builder_digest())
    for n in sorted(verdicts):
        v = verdicts[n]
        print(f"{n:24} {v.state:12} {'; '.join(v.reasons)}")
    return 0


def cmd_status(args):
    cfg = _cfg(args)
    st = state.State(cfg.db)
    buildd.refresh(cfg, st)
    for row in st.all():
        print(f"{row['name']:24} {row['version']:14} {row['state']:12} {row['reason'][:70]}")
    return 0


def cmd_bootstrap(args):
    cfg = _cfg(args)
    print("importing toolchain sysroot:")
    bootstrap.import_toolchain(cfg)
    return 0


def cmd_build(args):
    cfg = _cfg(args)
    st = state.State(cfg.db)
    buildd.refresh(cfg, st)
    row = st.get(args.source)
    if row is None:
        print(f"no such source: {args.source}", file=sys.stderr)
        return 1
    if row["state"] != "needs-build":
        print(f"{args.source} is {row['state']}: {row['reason']}", file=sys.stderr)
        return 1
    buildd.build_one(cfg, st, only=args.source, log=print)
    row = st.get(args.source)
    print(f"{args.source}: {row['state']}")
    return 0 if row["state"] == "built" else 1


def cmd_buildd(args):
    cfg = _cfg(args)
    st = state.State(cfg.db)
    while True:
        st.reap(cfg.build_timeout + 600)
        n = buildd.run(cfg, st, max_builds=args.max)
        if args.once or args.max is not None:
            print(f"built {n} source(s)")
            return 0
        time.sleep(args.interval)


def cmd_retry(args):
    st = state.State(_cfg(args).db)
    st.retry(args.source)
    return 0


def cmd_history(args):
    st = state.State(_cfg(args).db)
    for h in st.history(args.source):
        print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(h["at"])),
              f"{h['old'] or '-':12} -> {h['new']:12} {h['note'][:80]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="smechbuild")
    ap.add_argument("-c", "--config", default="smechbuild.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in [
        ("check", cmd_check, "validate every recipe and the build-dependency graph"),
        ("plan", cmd_plan, "show what needs building, without touching the queue"),
        ("status", cmd_status, "refresh the queue and show every source's state"),
        ("bootstrap", cmd_bootstrap, "import the cross toolchain's sysroot as the first packages"),
    ]:
        sub.add_parser(name, help=helptext).set_defaults(fn=fn)
    p = sub.add_parser("build", help="build one source that needs building")
    p.add_argument("source")
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("buildd", help="build everything that is ready")
    p.add_argument("--once", action="store_true", help="stop when nothing is ready")
    p.add_argument("--max", type=int, help="stop after N builds")
    p.add_argument("--interval", type=int, default=60)
    p.set_defaults(fn=cmd_buildd)
    for name, fn in (("retry", cmd_retry), ("history", cmd_history)):
        p = sub.add_parser(name)
        p.add_argument("source")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (recipe.RecipeError, ValueError, FileNotFoundError) as e:
        print(f"smechbuild: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
