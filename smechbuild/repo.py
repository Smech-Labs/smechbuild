"""The published repository: pool/, flat <name>.spkg, index.txt, index.meta, buildinfo/."""

import fcntl
import glob
import json
import os
import shutil
from contextlib import contextmanager

from . import spkg, version


@contextmanager
def lock(repo_dir):
    os.makedirs(repo_dir, exist_ok=True)
    with open(os.path.join(repo_dir, ".lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def scan(repo_dir):
    """{(name, version): control dict} for everything in pool/."""
    out = {}
    for p in sorted(glob.glob(os.path.join(repo_dir, "pool", "*.spkg"))):
        c = spkg.read_control(p)
        out[(c["name"], c["version"])] = c
    return out


def write_indexes(repo_dir):
    """Regenerate index.txt (latest per name) and index.meta (every version)."""
    pkgs = scan(repo_dir)
    latest = {}
    for (name, ver) in pkgs:
        if name not in latest or version.compare(ver, latest[name]) > 0:
            latest[name] = ver
    for name, ver in latest.items():
        shutil.copyfile(os.path.join(repo_dir, "pool", f"{name}_{ver}.spkg"),
                        os.path.join(repo_dir, f"{name}.spkg.tmp"))
        os.replace(os.path.join(repo_dir, f"{name}.spkg.tmp"), os.path.join(repo_dir, f"{name}.spkg"))
    _atomic_write(os.path.join(repo_dir, "index.txt"),
                  "".join(f"{n} {v}\n" for n, v in sorted(latest.items())))
    stanzas = []
    for (name, ver), c in sorted(pkgs.items(), key=lambda kv: (kv[0][0], version.sort_key(kv[0][1]))):
        stanzas.append(
            f"name: {name}\nversion: {ver}\ndepends: {c.get('depends', '')}\n"
            f"provides: {c.get('provides', '')}\nconflicts: {c.get('conflicts', '')}\n"
            f"source: {c.get('source', '')}\nabi: {c.get('abi', '')}\n"
        )
    _atomic_write(os.path.join(repo_dir, "index.meta"), "\n".join(stanzas))


def publish(repo_dir, outputs, buildinfo):
    """outputs: [(spkg path)]. Copies into pool/, records buildinfo, rewrites indexes."""
    with lock(repo_dir):
        os.makedirs(os.path.join(repo_dir, "pool"), exist_ok=True)
        os.makedirs(os.path.join(repo_dir, "buildinfo"), exist_ok=True)
        for path in outputs:
            c = spkg.read_control(path)
            dest = os.path.join(repo_dir, "pool", f"{c['name']}_{c['version']}.spkg")
            shutil.copyfile(path, dest + ".tmp")
            os.replace(dest + ".tmp", dest)
        _atomic_write(
            os.path.join(repo_dir, "buildinfo", f"{buildinfo['source']}_{buildinfo['version']}.json"),
            json.dumps(buildinfo, indent=2, sort_keys=True) + "\n",
        )
        write_indexes(repo_dir)


def load_buildinfos(repo_dir):
    """{source: buildinfo of its highest published version}."""
    best = {}
    for p in glob.glob(os.path.join(repo_dir, "buildinfo", "*.json")):
        with open(p) as f:
            bi = json.load(f)
        s = bi["source"]
        if s not in best or version.compare(bi["version"], best[s]["version"]) > 0:
            best[s] = bi
    return best


def load_repo_state(repo_dir):
    """{binary name: {"version", "abi", "source"}} for the latest of each name."""
    out = {}
    for (name, ver), c in scan(repo_dir).items():
        if name not in out or version.compare(ver, out[name]["version"]) > 0:
            out[name] = {"version": ver, "abi": c.get("abi", ""), "source": c.get("source", "")}
    return out
