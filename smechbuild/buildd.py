"""buildd: claim sources from the state DB, build them, publish, repeat."""

import socket
import traceback

from . import build as build_mod
from . import fetch, graph, package, recipe, repo, state, sysroot


def refresh(cfg, st):
    """Reload recipes and repo, recompute the plan, update the DB. Returns recipes."""
    recipes = recipe.load_all(cfg.pkgs)
    verdicts = graph.plan(recipes, repo.load_buildinfos(cfg.repo), repo.load_repo_state(cfg.repo),
                          builder_digest=build_mod.builder_digest())
    st.sync(recipes, verdicts, {n: recipe.digest(r) for n, r in recipes.items()})
    return recipes


def builder_id():
    return f"{socket.gethostname()}"


def build_one(cfg, st, only=None, log=print):
    """Claim and build one source. Returns its name, or None when nothing is ready."""
    recipes = refresh(cfg, st)
    name = st.claim(builder_id(), only=only)
    if name is None:
        return None
    r = recipes[name]
    try:
        outputs, buildinfo, log_path = build_mod.build(cfg, r, log=log)
        repo.publish(cfg.repo, outputs, buildinfo)
    except (build_mod.BuildError, fetch.FetchError, sysroot.SysrootError, package.PackagingError) as e:
        st.finish(name, False, str(e)[:2000])
        log(f"[{name}] FAILED: {e}")
    except Exception:  # a bug in smechbuild itself: record it, keep the daemon alive
        tb = traceback.format_exc()
        st.finish(name, False, "internal error:\n" + tb[-2000:])
        log(f"[{name}] INTERNAL ERROR:\n{tb}")
    else:
        st.finish(name, True, f"built {r.full_version}")
        log(f"[{name}] built {r.full_version}: {', '.join(o['name'] for o in buildinfo['outputs'])}")
    refresh(cfg, st)  # dependents may now be ready, or need a rebuild
    return name


def run(cfg, st, max_builds=None, log=print):
    done = 0
    while max_builds is None or done < max_builds:
        name = build_one(cfg, st, log=log)
        if name is None:
            break
        if st.get(name)["state"] == "built":
            done += 1
    return done
