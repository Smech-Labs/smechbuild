"""Build one source package end to end (the sbuild role)."""

import json
import os
import shutil
import subprocess
import tarfile

from . import fetch, package, sandbox, shlibs, spkg, sysroot
from . import recipe as recipe_mod


class BuildError(Exception):
    pass


def unpack(archive, dest, strip=1):
    with tarfile.open(archive) as t:
        members = []
        for m in t.getmembers():
            parts = [p for p in m.name.split("/") if p not in ("", ".")]
            if len(parts) <= strip:
                continue
            m.name = "/".join(parts[strip:])
            if m.islnk():
                lp = [p for p in m.linkname.split("/") if p not in ("", ".")]
                m.linkname = "/".join(lp[strip:])
            members.append(m)
        t.extractall(dest, members=members, filter="data")


def builder_digest():
    """Hash of the build scripts themselves: a change rebuilds everything."""
    import hashlib
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "runner.sh"), "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def _image_id(cfg):
    r = subprocess.run(["podman", "image", "inspect", "--format", "{{.Id}}", cfg.image],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def build(cfg, r, log=print):
    """Returns (list of .spkg paths, buildinfo dict, log path). Raises BuildError."""
    wd = os.path.join(cfg.work, f"{r.name}_{r.full_version}")
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd)
    dirs = sysroot.prepare_dirs(wd)
    out_dir = os.path.join(wd, "out")
    os.makedirs(out_dir)
    log_path = os.path.join(wd, "build.log")

    log(f"[{r.name}] fetching sources")
    archives = [fetch.fetch(s, cfg.cache) for s in r.sources]
    unpack(archives[0], dirs["src"])
    if not os.listdir(dirs["src"]):
        raise BuildError(f"{r.sources[0].filename} unpacked to nothing")
    if len(archives) > 1:
        extra = os.path.join(wd, "extra")
        os.makedirs(extra)
        for s, a in zip(r.sources[1:], archives[1:]):
            shutil.copyfile(a, os.path.join(extra, s.filename))
        dirs["extra"] = extra

    log(f"[{r.name}] resolving build-dependencies: {', '.join(r.build_depends) or 'none'}")
    plan = sysroot.solve(cfg, r, wd)
    deps, ext_sonames = sysroot.assemble(cfg, plan, dirs["sysroot"])
    log(f"[{r.name}] sysroot: {len(plan)} package(s)")

    env = {
        "BUILD_SYSTEM": r.build_system, "TRIPLET": cfg.triplet, "JOBS": str(cfg.jobs),
        "PREFIX": "/usr", "LIBDIR": "/usr/lib", "CONFIGURE_ARGS": r.configure_args,
    }
    log(f"[{r.name}] building in sandbox (log: {log_path})")
    try:
        sandbox.run(cfg, r, dirs, env, log_path, f"smechbuild-{r.name}")
    except sandbox.SandboxError as e:
        raise BuildError(str(e)) from None

    log(f"[{r.name}] splitting into packages")
    try:
        claimed = package.split(dirs["destdir"], r.packages)
    except package.PackagingError as e:
        raise BuildError(f"packaging {r.name}:\n{e}") from None

    payloads, own_sonames, per_pkg_sonames = {}, {}, {}
    for pkg in r.packages:
        pd = os.path.join(dirs["pkg"], pkg.name)
        package.materialize(dirs["destdir"], claimed[pkg.name], pd)
        payloads[pkg.name] = pd
        provided = shlibs.soname_providers(pd)
        per_pkg_sonames[pkg.name] = sorted(provided)
        for so in provided:
            own_sonames[so] = pkg.name

    outputs, out_records = [], []
    for pkg in r.packages:
        pd = payloads[pkg.name]
        gen, unresolved = package.shlib_depends(pd, own_sonames, ext_sonames)
        if unresolved:
            raise BuildError(
                f"packaging {pkg.name}: shared libraries not provided by any build-dependency:\n  "
                + "\n  ".join(unresolved[:20]))
        depends = package.merge_depends(pkg.depends, gen, pkg.name, r.full_version)
        abi = shlibs.abi_key(pd, r.full_version)
        control = package.build_control(r, pkg, depends, pd, "x86_64", abi, per_pkg_sonames[pkg.name])
        path = os.path.join(out_dir, f"{pkg.name}_{r.full_version}.spkg")
        spkg.write(path, control, pd)
        outputs.append(path)
        out_records.append({"name": pkg.name, "sha256": spkg.file_sha256(path), "abi": abi})

    buildinfo = {
        "source": r.name,
        "version": r.full_version,
        "recipe_digest": recipe_mod.digest(r),
        "builder_digest": builder_digest(),
        "sources": [{"url": s.url, "sha256": s.sha256} for s in r.sources],
        "build_depends": deps,
        "triplet": cfg.triplet,
        "image": _image_id(cfg),
        "jobs": cfg.jobs,
        "outputs": out_records,
    }
    with open(os.path.join(out_dir, "buildinfo.json"), "w") as f:
        json.dump(buildinfo, f, indent=2, sort_keys=True)
    return outputs, buildinfo, log_path
