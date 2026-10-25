"""ELF inspection: NEEDED sonames (-> Depends) and ABI keys (-> rebuild triggers)."""

import hashlib
import os
import re
import subprocess

_MAGIC = b"\x7fELF"


def is_elf(path: str) -> bool:
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    with open(path, "rb") as f:
        return f.read(4) == _MAGIC


def _readelf(args, path):
    r = subprocess.run(["readelf", *args, path], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def needed(path: str):
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", _readelf(["-d", "-W"], path))


def soname(path: str):
    m = re.search(r"\(SONAME\)\s+Library soname: \[([^\]]+)\]", _readelf(["-d", "-W"], path))
    return m.group(1) if m else None


def defined_versions(path: str):
    """Symbol-version tags a library defines (e.g. Qt_6.11.2_PRIVATE_API)."""
    out = _readelf(["-V", "-W"], path)
    tags = set()
    in_def = False
    for line in out.splitlines():
        if line.startswith("Version definition"):
            in_def = True
        elif line.startswith("Version needs"):
            in_def = False
        elif in_def:
            m = re.search(r"Name:\s+(\S+)", line)
            if m:
                tags.add(m.group(1))
    return sorted(tags)


def abi_key(payload_dir: str, version: str) -> str:
    """Hash of every SONAME and symbol-version tag the package exports. A
    package with no shared libraries falls back to its exact version, so data
    and header-only packages change ABI on every release (conservative)."""
    items = []
    for root, _dirs, files in os.walk(payload_dir):
        for f in files:
            p = os.path.join(root, f)
            if is_elf(p):
                so = soname(p)
                if so:
                    items.append(f"{so}|{','.join(defined_versions(p))}")
    if not items:
        items = [f"version:{version}"]
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]


def soname_providers(payload_dir: str):
    """{soname: relative path} for every shared library in a payload."""
    out = {}
    for root, _dirs, files in os.walk(payload_dir):
        for f in files:
            p = os.path.join(root, f)
            if is_elf(p):
                so = soname(p)
                if so:
                    out[so] = os.path.relpath(p, payload_dir)
    return out
