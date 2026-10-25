"""Split DESTDIR into binary packages and write their .spkg files.

Same discipline as dh_missing --fail-missing: every installed file must be
claimed by exactly one package, and every Files glob must match something.
"""

import glob
import os
import shutil

from . import recipe as recipe_mod
from . import shlibs, spkg, version


class PackagingError(Exception):
    pass


def _entries(destdir):
    """Relative paths of every file and symlink (directories are implied)."""
    out = set()
    for root, dirs, files in os.walk(destdir):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            out.add(os.path.relpath(os.path.join(root, name), destdir))
    return out


def split(destdir, packages):
    """{package name: sorted relative paths}. Raises PackagingError on any
    overlap, unmatched glob, or unclaimed file."""
    entries = _entries(destdir)
    claimed, owner = {}, {}
    problems = []
    for pkg in packages:
        mine = set()
        for pattern in pkg.files:
            matches = glob.glob(pattern, root_dir=destdir)
            if not matches:
                problems.append(f"{pkg.name}: Files glob {pattern!r} matched nothing")
            for m in matches:
                full = os.path.join(destdir, m)
                if os.path.isdir(full) and not os.path.islink(full):
                    prefix = m.rstrip("/") + "/"
                    mine |= {e for e in entries if e.startswith(prefix)}
                elif m in entries:
                    mine.add(m)
        for e in mine:
            if e in owner and owner[e] != pkg.name:
                problems.append(f"{e} is claimed by both {owner[e]} and {pkg.name}")
            owner[e] = pkg.name
        claimed[pkg.name] = sorted(mine)
    leftover = sorted(entries - set(owner))
    if leftover:
        shown = "\n  ".join(leftover[:40])
        more = f"\n  ... and {len(leftover) - 40} more" if len(leftover) > 40 else ""
        problems.append(f"{len(leftover)} installed file(s) belong to no package:\n  {shown}{more}")
    if problems:
        raise PackagingError("\n".join(problems))
    return claimed


def materialize(destdir, rels, payload_dir):
    """Copy the claimed paths into payload_dir, preserving modes and symlinks."""
    os.makedirs(payload_dir, exist_ok=True)
    for rel in rels:
        src, dst = os.path.join(destdir, rel), os.path.join(payload_dir, rel)
        _mkparents(destdir, payload_dir, os.path.dirname(rel))
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)


def _mkparents(srcroot, dstroot, rel_dir):
    cur = ""
    for part in [p for p in rel_dir.split(os.sep) if p]:
        cur = os.path.join(cur, part)
        d = os.path.join(dstroot, cur)
        if not os.path.isdir(d):
            os.mkdir(d)
            os.chmod(d, os.stat(os.path.join(srcroot, cur)).st_mode & 0o7777 | 0o200)


def installed_size_kib(payload_dir):
    total = 0
    for root, _d, files in os.walk(payload_dir):
        for f in files:
            p = os.path.join(root, f)
            if not os.path.islink(p):
                total += os.path.getsize(p)
    return (total + 1023) // 1024


def shlib_depends(payload_dir, own_sonames, external_sonames, ignore=()):
    """Dependencies implied by ELF NEEDED entries.

    own_sonames: {soname: package} provided by this package or its siblings
    external_sonames: {soname: (package, version)} from the build sysroot
    Returns (list of (package, version-or-None, is_sibling), unresolved list).
    """
    deps, unresolved = {}, []
    for root, _d, files in os.walk(payload_dir):
        for f in sorted(files):
            p = os.path.join(root, f)
            if not shlibs.is_elf(p):
                continue
            for so in shlibs.needed(p):
                if so in ignore:
                    continue
                if so in own_sonames:
                    deps[own_sonames[so]] = (None, True)
                elif so in external_sonames:
                    pkg, ver = external_sonames[so]
                    prev = deps.get(pkg)
                    if prev is None or (ver and (not prev[0] or version.compare(ver, prev[0]) > 0)):
                        deps[pkg] = (ver, False)
                else:
                    unresolved.append(f"{os.path.relpath(p, payload_dir)} needs {so}")
    return deps, unresolved


def merge_depends(manual: str, generated: dict, self_name: str, full_version: str):
    """Hand-written Depends win; generated ones fill in the rest."""
    manual = manual.replace("${binary:Version}", full_version)
    out = recipe_mod.split_deps(manual)
    have = {d.split("(")[0].split("|")[0].strip() for d in out}
    for pkg in sorted(generated):
        if pkg == self_name or pkg in have:
            continue
        ver, sibling = generated[pkg]
        if sibling:
            out.append(f"{pkg} (= {full_version})")
        elif ver:
            out.append(f"{pkg} (>= {ver})")
        else:
            out.append(pkg)
    return ", ".join(out)


def build_control(recipe, pkg, depends, payload_dir, architecture, abi, sonames):
    return {
        "name": pkg.name,
        "version": recipe.full_version,
        "architecture": architecture,
        "maintainer": recipe.maintainer,
        "installed-size": str(installed_size_kib(payload_dir)),
        "homepage": recipe.homepage,
        "source": recipe.name,
        "depends": depends,
        "provides": pkg.provides,
        "conflicts": pkg.conflicts,
        "description": pkg.description or pkg.name,
        "abi": abi,
        "sonames": ",".join(sorted(sonames)),
    }
