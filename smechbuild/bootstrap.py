"""Stage 1 bootstrap: turn the cross toolchain's sysroot into the first .spkg files.

glibc, the kernel headers and the GCC runtime libraries come from the
crosstool-ng build, not from a recipe, so they are imported once. Everything
else is built by recipes against these packages.
"""

import os
import re

from . import package, repo, shlibs, spkg

_KERNEL_HEADER_DIRS = ("asm", "asm-generic", "drm", "linux", "misc", "mtd", "rdma",
                       "regulator", "scsi", "sound", "video", "xen")


def classify(rel: str) -> str:
    """Which imported package owns this sysroot-relative path."""
    parts = rel.split("/")
    base = parts[-1]
    if parts[:2] == ["usr", "include"] and len(parts) > 2:
        return "linux-api-headers" if parts[2] in _KERNEL_HEADER_DIRS else "libc-dev"
    if rel == "lib/libgcc_s.so.1":
        return "libgcc-s1"
    if parts[:1] == ["lib"] and re.match(r"^libstdc\+\+\.so\.6(\.\d+)*$", base):
        return "libstdc++6"
    if parts[:2] in (["usr", "lib"], ["lib"]) and (
        base.endswith((".a", ".o")) or base.endswith(".so") or base.endswith("-gdb.py")
    ):
        return "libc-dev"
    return "glibc"


def _version_of(sysroot):
    glibc = "unknown"
    for f in os.listdir(os.path.join(sysroot, "usr", "lib")):
        m = re.match(r"^libm-(\d+\.\d+)\.a$", f)
        if m:
            glibc = m.group(1)
    code = None
    with open(os.path.join(sysroot, "usr", "include", "linux", "version.h")) as fh:
        for line in fh:
            if "LINUX_VERSION_CODE" in line:
                code = int(line.split()[-1])
    kernel = f"{code >> 16}.{(code >> 8) & 255}.{code & 255}" if code else "unknown"
    return glibc, kernel


def import_toolchain(cfg, log=print):
    if not (cfg.toolchain and cfg.triplet):
        raise ValueError("bootstrap needs 'toolchain' and 'triplet' in the config")
    sysroot = os.path.join(cfg.toolchain, cfg.triplet, "sysroot")
    if not os.path.isdir(sysroot):
        raise FileNotFoundError(sysroot)
    glibc_v, kernel_v = _version_of(sysroot)
    gcc_v = "14.2.0"
    versions = {"glibc": f"{glibc_v}-1", "libc-dev": f"{glibc_v}-1",
                "linux-api-headers": f"{kernel_v}-1",
                "libgcc-s1": f"{gcc_v}-1", "libstdc++6": f"{gcc_v}-1"}
    deps = {"glibc": "", "libc-dev": f"glibc (= {versions['glibc']}), linux-api-headers",
            "linux-api-headers": "", "libgcc-s1": f"glibc (>= {glibc_v})",
            "libstdc++6": f"glibc (>= {glibc_v}), libgcc-s1"}
    desc = {"glibc": "GNU C library runtime (imported from the cross toolchain)",
            "libc-dev": "GNU C library headers and link-time files",
            "linux-api-headers": "Linux kernel userspace API headers",
            "libgcc-s1": "GCC support library (libgcc_s)",
            "libstdc++6": "GNU C++ standard library runtime"}

    buckets = {k: [] for k in versions}
    for root, dirs, files in os.walk(sysroot):
        names = files + [d for d in dirs if os.path.islink(os.path.join(root, d))]
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), sysroot)
            buckets[classify(rel)].append(rel)

    work = os.path.join(cfg.work, "bootstrap")
    os.makedirs(work, exist_ok=True)
    outputs = []
    for name, rels in sorted(buckets.items()):
        if not rels:
            raise RuntimeError(f"bootstrap: no files for {name}; the toolchain layout changed")
        pd = os.path.join(work, name)
        _rmtree(pd)
        package.materialize(sysroot, sorted(rels), pd)
        sonames = shlibs.soname_providers(pd)
        control = {
            "name": name, "version": versions[name], "architecture": "x86_64",
            "maintainer": "Smech Labs <smech@smech.xyz>",
            "installed-size": str(package.installed_size_kib(pd)),
            "homepage": "https://smech.xyz", "source": "toolchain-import",
            "depends": deps[name], "provides": "", "conflicts": "",
            "description": desc[name],
            "abi": shlibs.abi_key(pd, versions[name]), "sonames": ",".join(sorted(sonames)),
        }
        path = os.path.join(work, f"{name}_{versions[name]}.spkg")
        spkg.write(path, control, pd)
        outputs.append(path)
        log(f"  {name} {versions[name]}: {len(rels)} files")
    repo.publish(cfg.repo, outputs, {
        "source": "toolchain-import", "version": f"{glibc_v}-1", "bootstrap": True,
        "triplet": cfg.triplet, "recipe_digest": "", "build_depends": [],
        "outputs": [{"name": os.path.basename(p)} for p in outputs],
    })
    return outputs


def _rmtree(path):
    import shutil

    def onerror(func, p, _exc):
        os.chmod(os.path.dirname(p), 0o755)
        os.chmod(p, 0o755)
        func(p)
    shutil.rmtree(path, onerror=onerror) if os.path.exists(path) else None
