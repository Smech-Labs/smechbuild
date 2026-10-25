"""Source package recipes: pkgs/<source>/control (+ rules, patches)."""

import os
import re
from dataclasses import dataclass, field

BUILD_SYSTEMS = ("autotools", "cmake", "meson", "custom")
_NAME = re.compile(r"^[a-z0-9][a-z0-9+.\-]*$")
_VERSION = re.compile(r"^[0-9][A-Za-z0-9.+~]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read(path):
    with open(path) as f:
        return f.read()


class RecipeError(Exception):
    pass


@dataclass
class SourceFile:
    sha256: str
    size: int
    url: str
    filename: str


@dataclass
class BinaryPackage:
    name: str
    description: str = ""
    depends: str = ""  # extra, hand-written; shlibs are added at build time
    provides: str = ""
    conflicts: str = ""
    files: list = field(default_factory=list)


@dataclass
class Recipe:
    name: str
    version: str
    revision: int
    maintainer: str
    homepage: str
    build_system: str
    build_depends: list  # dependency strings, e.g. "m4", "qt6-base (>= 6.11)"
    sources: list
    packages: list
    configure_args: str
    path: str

    @property
    def full_version(self) -> str:
        return f"{self.version}-{self.revision}"

    def rules_path(self):
        p = os.path.join(self.path, "rules")
        return p if os.path.isfile(p) else None

    def patches(self):
        series = os.path.join(self.path, "patches", "series")
        if not os.path.isfile(series):
            return []
        out = []
        for line in _read(series).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(os.path.join(self.path, "patches", line))
        return out


def parse_deb822(text: str):
    """Return a list of stanzas; each is a dict of lowercase key -> str.
    Continuation lines (leading space) are joined with newlines."""
    stanzas, cur, key = [], {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("#"):
            continue
        if not raw.strip():
            if cur:
                stanzas.append(cur)
                cur, key = {}, None
            continue
        if raw[0] in " \t":
            if key is None:
                raise RecipeError(f"line {n}: continuation with no field")
            cur[key] += "\n" + raw.strip()
            continue
        if ":" not in raw:
            raise RecipeError(f"line {n}: expected 'Field: value', got {raw!r}")
        k, v = raw.split(":", 1)
        key = k.strip().lower()
        if key in cur:
            raise RecipeError(f"line {n}: duplicate field {k.strip()!r}")
        cur[key] = v.strip()
    if cur:
        stanzas.append(cur)
    return stanzas


def split_deps(s: str):
    """'a, b (>= 1) | c' -> ['a', 'b (>= 1) | c']"""
    return [d.strip() for d in s.replace("\n", " ").split(",") if d.strip()]


def _require(stanza, field_name, where):
    v = stanza.get(field_name, "")
    if not v:
        raise RecipeError(f"{where}: missing required field {field_name!r}")
    return v


def load(path: str) -> Recipe:
    """Load and validate pkgs/<name>/ (path is the directory)."""
    ctl = os.path.join(path, "control")
    if not os.path.isfile(ctl):
        raise RecipeError(f"{path}: no control file")
    try:
        with open(ctl) as f:
            stanzas = parse_deb822(f.read())
    except RecipeError as e:
        raise RecipeError(f"{ctl}: {e}") from None
    if len(stanzas) < 2:
        raise RecipeError(f"{ctl}: need a Source stanza and at least one Package stanza")
    src = stanzas[0]
    where = ctl
    name = _require(src, "source", where)
    if not _NAME.match(name):
        raise RecipeError(f"{where}: bad source name {name!r}")
    if os.path.basename(os.path.normpath(path)) != name:
        raise RecipeError(f"{where}: directory name must equal Source ({name})")
    version = _require(src, "version", where)
    if not _VERSION.match(version):
        raise RecipeError(f"{where}: bad Version {version!r}")
    try:
        revision = int(src.get("revision", "1"))
    except ValueError:
        raise RecipeError(f"{where}: Revision must be an integer") from None
    if revision < 1:
        raise RecipeError(f"{where}: Revision must be >= 1")
    bs = src.get("build-system", "")
    if bs not in BUILD_SYSTEMS:
        raise RecipeError(f"{where}: Build-System must be one of {BUILD_SYSTEMS}")
    if bs == "custom" and not os.path.isfile(os.path.join(path, "rules")):
        raise RecipeError(f"{where}: Build-System: custom requires a rules file")

    sources = []
    for line in src.get("sources", "").strip().splitlines():
        parts = line.split()
        if len(parts) not in (3, 4):
            raise RecipeError(f"{where}: Sources line needs 'sha256 size url [filename]': {line!r}")
        sha, size, url = parts[:3]
        if not _SHA256.match(sha):
            raise RecipeError(f"{where}: bad sha256 in Sources line {line!r}")
        if not size.isdigit():
            raise RecipeError(f"{where}: bad size in Sources line {line!r}")
        if not re.match(r"^(https?|ftp)://", url):
            raise RecipeError(f"{where}: Sources url must be http(s)/ftp: {url!r}")
        fn = parts[3] if len(parts) == 4 else url.rsplit("/", 1)[-1]
        if "/" in fn or fn in ("", ".", ".."):
            raise RecipeError(f"{where}: bad source filename {fn!r}")
        sources.append(SourceFile(sha, int(size), url, fn))
    if not sources:
        raise RecipeError(f"{where}: at least one Sources entry is required")

    packages, seen = [], set()
    for st in stanzas[1:]:
        pname = _require(st, "package", where)
        if not _NAME.match(pname):
            raise RecipeError(f"{where}: bad package name {pname!r}")
        if pname in seen:
            raise RecipeError(f"{where}: duplicate package {pname!r}")
        seen.add(pname)
        files = [f.strip() for f in st.get("files", "").splitlines() if f.strip()]
        if not files:
            raise RecipeError(f"{where}: package {pname} has no Files")
        for f in files:
            if f.startswith("/") or ".." in f.split("/"):
                raise RecipeError(f"{where}: Files glob must be relative, no '..': {f!r}")
        packages.append(
            BinaryPackage(
                name=pname,
                description=st.get("description", "").splitlines()[0] if st.get("description") else "",
                depends=st.get("depends", ""),
                provides=st.get("provides", ""),
                conflicts=st.get("conflicts", ""),
                files=files,
            )
        )

    return Recipe(
        name=name,
        version=version,
        revision=revision,
        maintainer=src.get("maintainer", "Smech Labs <smech@smech.xyz>"),
        homepage=src.get("homepage", ""),
        build_system=bs,
        build_depends=split_deps(src.get("build-depends", "")),
        sources=sources,
        packages=packages,
        configure_args=src.get("configure-args", ""),
        path=os.path.abspath(path),
    )


def load_all(pkgs_dir: str):
    """Load every recipe under pkgs_dir; returns {name: Recipe}. Errors are
    collected so one bad recipe is reported with all the others."""
    out, errors = {}, []
    for d in sorted(os.listdir(pkgs_dir)):
        p = os.path.join(pkgs_dir, d)
        if not os.path.isdir(p):
            continue
        try:
            r = load(p)
        except RecipeError as e:
            errors.append(str(e))
            continue
        out[r.name] = r
    # binary-name collisions across sources
    owner = {}
    for r in out.values():
        for b in r.packages:
            if b.name in owner and owner[b.name] != r.name:
                errors.append(f"binary package {b.name} defined by both {owner[b.name]} and {r.name}")
            owner[b.name] = r.name
    if errors:
        raise RecipeError("\n".join(errors))
    return out


def digest(recipe: Recipe) -> str:
    """sha256 over everything that defines how the source is built: the
    control file, rules, and every patch. A change makes the source rebuild."""
    import hashlib

    h = hashlib.sha256()
    files = ["control", "rules"]
    pdir = os.path.join(recipe.path, "patches")
    if os.path.isdir(pdir):
        files += [os.path.join("patches", f) for f in sorted(os.listdir(pdir))]
    for rel in files:
        p = os.path.join(recipe.path, rel)
        if os.path.isfile(p):
            h.update(rel.encode() + b"\0")
            with open(p, "rb") as f:
                h.update(f.read())
            h.update(b"\0")
    return h.hexdigest()
