"""Assemble a build sysroot from published .spkg files (sbuild's chroot setup)."""

import os
import shutil
import subprocess

from . import recipe as recipe_mod
from . import spkg


class SysrootError(Exception):
    pass


def _synthetic_name(source):
    return f"build-env-{source}"


def solve(cfg, recipe, workdir):
    """Ask spk's solver for the exact closure of the recipe's Build-Depends.
    Returns [(name, version)] in install order, excluding the synthetic package."""
    if not recipe.build_depends:
        return []
    meta_path = os.path.join(cfg.repo, "index.meta")
    if not os.path.isfile(meta_path):
        raise SysrootError(f"{recipe.name}: repo has no index.meta, cannot resolve Build-Depends")
    envrepo = os.path.join(workdir, "envrepo")
    os.makedirs(envrepo, exist_ok=True)
    synth = _synthetic_name(recipe.name)
    with open(meta_path) as f:
        meta = f.read()
    with open(os.path.join(envrepo, "index.meta"), "w") as f:
        f.write(meta.rstrip("\n") + "\n\n")
        f.write(f"name: {synth}\nversion: 1\ndepends: {', '.join(recipe.build_depends)}\n"
                "provides: \nconflicts: \n")
    conf = os.path.join(workdir, "spk-repos.yaml")
    with open(conf, "w") as f:
        f.write(f"repos:\n  - name: build\n    url: file://{envrepo}\n    priority: 1\n")
    root = os.path.join(workdir, "spkroot")
    os.makedirs(root, exist_ok=True)
    env = dict(os.environ, SPK_REPO_CONF=conf, SPK_ROOT=root)
    r = subprocess.run([cfg.spk, "solve", "--porcelain", synth], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SysrootError(f"{recipe.name}: Build-Depends cannot be satisfied: {r.stderr.strip()}")
    plan = [tuple(line.split(" ", 1)) for line in r.stdout.splitlines() if line.strip()]
    return [(n, v) for n, v in plan if n != synth]


def pool_path(cfg, name, version):
    return os.path.join(cfg.repo, "pool", f"{name}_{version}.spkg")


def assemble(cfg, plan, sysroot):
    """Extract every package of the plan into sysroot. Returns
    (build_depends records, {soname: (package, version)})."""
    records, sonames = [], {}
    for name, version in plan:
        path = pool_path(cfg, name, version)
        if not os.path.isfile(path):
            raise SysrootError(f"{name} {version} is in index.meta but {path} does not exist")
        ctl = spkg.read_control(path)
        spkg.extract_data(path, sysroot)
        records.append({
            "name": name, "version": version,
            "sha256": spkg.file_sha256(path), "abi": ctl.get("abi", ""),
        })
        for so in filter(None, ctl.get("sonames", "").split(",")):
            sonames[so] = (name, version)
    return records, sonames


def prepare_dirs(workdir):
    dirs = {k: os.path.join(workdir, k) for k in ("src", "obj", "destdir", "sysroot", "pkg")}
    for d in dirs.values():
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    return dirs
