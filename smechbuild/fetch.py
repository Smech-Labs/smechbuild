"""Source fetching with sha256 pinning. Runs on the host, never in the sandbox."""

import hashlib
import os
import urllib.request


class FetchError(Exception):
    pass


def cached_path(cache_dir: str, src) -> str:
    return os.path.join(cache_dir, "sources", src.sha256)


def fetch(src, cache_dir: str) -> str:
    """Return the cached file for `src`, downloading and verifying if needed.
    A file whose hash or size differs from the recipe is deleted and refused."""
    dest = cached_path(cache_dir, src)
    if os.path.isfile(dest) and _verify(dest, src):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    h = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(src.url, timeout=60) as r, open(tmp, "wb") as out:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
                out.write(chunk)
        if h.hexdigest() != src.sha256 or size != src.size:
            raise FetchError(
                f"{src.url}: got sha256 {h.hexdigest()} ({size} bytes), "
                f"recipe says {src.sha256} ({src.size} bytes)"
            )
        os.replace(tmp, dest)
    except OSError as e:
        raise FetchError(f"{src.url}: {e}") from None
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def _verify(path, src) -> bool:
    if os.path.getsize(path) != src.size:
        os.unlink(path)
        return False
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != src.sha256:
        os.unlink(path)
        return False
    return True
