""".spkg: outer tar holding control.tar.xz (control, sha256sums, hooks) and data.tar.xz."""

import hashlib
import io
import os
import tarfile


class SpkgError(Exception):
    pass


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_control(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def read_control(path: str) -> dict:
    with tarfile.open(path) as outer:
        member = outer.extractfile("control.tar.xz")
        if member is None:
            raise SpkgError(f"{path}: no control.tar.xz")
        with tarfile.open(fileobj=io.BytesIO(member.read()), mode="r:xz") as ctl:
            for m in ctl:
                if os.path.basename(m.name) == "control":
                    return parse_control(ctl.extractfile(m).read().decode())
    raise SpkgError(f"{path}: no control file")


def extract_data(path: str, dest: str):
    """Extract data.tar.xz into dest. Members that would land outside dest, and
    device nodes, are refused (the 'data' filter); the sandbox sees only dest."""
    with tarfile.open(path) as outer:
        member = outer.extractfile("data.tar.xz")
        if member is None:
            raise SpkgError(f"{path}: no data.tar.xz")
        with tarfile.open(fileobj=io.BytesIO(member.read()), mode="r:xz") as data:
            data.extractall(dest, filter="data")


def list_data(path: str):
    with tarfile.open(path) as outer:
        member = outer.extractfile("data.tar.xz")
        with tarfile.open(fileobj=io.BytesIO(member.read()), mode="r:xz") as data:
            return [(m.name.lstrip("./"), m.isdir()) for m in data if m.name not in (".", "./")]


def write(path: str, control: dict, payload_dir: str, hooks=None):
    """Write a .spkg. Deterministic: sorted members, zeroed mtimes, root owner."""
    hooks = hooks or {}

    def norm(ti):
        ti.mtime = 0
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        return ti

    def tar_xz(add):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz", format=tarfile.PAX_FORMAT) as t:
            add(t)
        return buf.getvalue()

    sums = []
    for root, dirs, files in os.walk(payload_dir):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(root, f)
            if os.path.islink(full):
                continue
            sums.append(f"{file_sha256(full)}  {os.path.relpath(full, payload_dir)}\n")
    sums.sort(key=lambda s: s.split("  ", 1)[1])

    def add_data(t):
        t.add(payload_dir, arcname=".", recursive=False, filter=norm)
        for root, dirs, files in os.walk(payload_dir):
            dirs.sort()
            for name in sorted(dirs) + sorted(files):
                full = os.path.join(root, name)
                t.add(full, arcname="./" + os.path.relpath(full, payload_dir), recursive=False, filter=norm)

    def add_ctl(t):
        def put(name, content, mode=0o644):
            ti = norm(tarfile.TarInfo("./" + name))
            ti.size, ti.mode = len(content), mode
            t.addfile(ti, io.BytesIO(content))
        put("control", "".join(f"{k}: {v}\n" for k, v in control.items()).encode())
        put("sha256sums", "".join(sums).encode())
        for hname, body in sorted(hooks.items()):
            put(hname, body.encode(), 0o755)

    ctl_bytes, data_bytes = tar_xz(add_ctl), tar_xz(add_data)
    tmp = path + ".tmp"
    with tarfile.open(tmp, "w", format=tarfile.PAX_FORMAT) as outer:
        for name, blob in (("control.tar.xz", ctl_bytes), ("data.tar.xz", data_bytes)):
            ti = norm(tarfile.TarInfo(name))
            ti.size, ti.mode = len(blob), 0o644
            outer.addfile(ti, io.BytesIO(blob))
    os.replace(tmp, path)
