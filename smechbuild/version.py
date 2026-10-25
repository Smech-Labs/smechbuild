"""Debian-style version comparison (same algorithm as dpkg and spk's resolver)."""

from functools import cmp_to_key


def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _cmp_part(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        # non-digit prefix
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ca = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            cb = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ca != cb:
                return -1 if ca < cb else 1
            if i < len(a) and not a[i].isdigit():
                i += 1
            if j < len(b) and not b[j].isdigit():
                j += 1
        # digit run
        ni = i
        while ni < len(a) and a[ni].isdigit():
            ni += 1
        nj = j
        while nj < len(b) and b[nj].isdigit():
            nj += 1
        na = int(a[i:ni] or 0)
        nb = int(b[j:nj] or 0)
        if na != nb:
            return -1 if na < nb else 1
        i, j = ni, nj
    return 0


def _split(v: str):
    epoch = 0
    if ":" in v:
        e, v = v.split(":", 1)
        epoch = int(e)
    if "-" in v:
        up, rev = v.rsplit("-", 1)
    else:
        up, rev = v, ""
    return epoch, up, rev


def compare(a: str, b: str) -> int:
    """-1, 0 or 1 like dpkg --compare-versions."""
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    c = _cmp_part(ua, ub)
    if c:
        return c
    return _cmp_part(ra, rb)


sort_key = cmp_to_key(compare)
