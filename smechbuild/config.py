"""Configuration (smechbuild.toml). Every path is explicit; nothing is guessed."""

import os
import tomllib
from dataclasses import dataclass


@dataclass
class Config:
    pkgs: str            # recipe tree
    repo: str            # published packages (<name>.spkg, pool/, index.txt, index.meta, buildinfo/)
    work: str            # per-build scratch
    cache: str           # source tarball cache
    db: str              # wanna-build sqlite file
    spk: str = "spk"     # package manager binary (its solver resolves build-deps)
    image: str = "localhost/smechos-builder:latest"
    triplet: str = ""    # empty = native build
    toolchain: str = ""  # host dir mounted read-only at /toolchain (cross toolchain root)
    jobs: int = 2
    build_timeout: int = 4 * 3600


def load(path: str) -> Config:
    base = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    unknown = set(raw) - set(Config.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
    for k in ("pkgs", "repo", "work", "cache", "db"):
        if k not in raw:
            raise ValueError(f"{path}: missing required setting {k!r}")
        raw[k] = os.path.normpath(os.path.join(base, raw[k]))
    if raw.get("toolchain"):
        raw["toolchain"] = os.path.normpath(os.path.join(base, raw["toolchain"]))
    return Config(**raw)
