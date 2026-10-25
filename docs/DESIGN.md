# smechbuild design

smechbuild builds SmechOS packages from source, one source package at a time,
in a clean sandbox, and keeps a queue of what needs (re)building. It copies
Debian's pipeline on purpose; the table says which Debian piece each part
replaces.

| Debian | smechbuild | Where |
|---|---|---|
| `debian/control` + `debian/rules` | `pkgs/<src>/control` + `rules` | `recipe.py` |
| orig tarball + checksums | `Sources:` with sha256, content-addressed cache | `fetch.py` |
| sbuild / schroot | rootless podman, `--network=none`, fresh sysroot per build | `sandbox.py`, `build.py` |
| `dpkg-shlibdeps` | `Depends` generated from ELF `NEEDED` | `shlibs.py` |
| `dh_missing --fail-missing` | files left over after splitting fail the build | `package.py` |
| `.buildinfo` | JSON record of every build-dependency version, hash and ABI | `buildinfo.py` |
| wanna-build | sqlite state DB with a state machine and history | `state.py` |
| buildd | claims `needs-build` sources, builds, handles Dep-Wait and failure | `buildd.py` |
| transitions / binNMU | automatic rebuild when a build-dependency's ABI changes | `graph.py` |
| dak / reprepro | pool + `index.txt` + `index.meta` (what `spk` reads) | `repo.py` |

## Rules

1. **Nothing is installed into a shared tree.** Each build gets its own
   sysroot, assembled from `.spkg` files of its Build-Depends closure, and its
   own `DESTDIR`. Packages are what the build installed, not what a diff
   guessed.
2. **Every input is pinned.** Source archives by sha256, build-dependencies by
   exact version and `.spkg` hash (recorded in the buildinfo).
3. **Failure is explicit.** A step that fails stops the build and records why.
   There is no log-and-continue.
4. **Rebuilds are driven by ABI, not by hope.** Every binary package records an
   ABI key (SONAMEs plus symbol-version tags it defines). If a build-dependency's
   ABI key differs from the one in a source's buildinfo, the source becomes
   `needs-build`. This is the check that would have caught 71 KF6 packages
   built against an older Qt.
5. **The sandbox has no network.** All sources come through the fetch cache
   before the sandbox starts.

## Source package layout

```
pkgs/gmp/control     deb822: one Source stanza, then one stanza per binary package
pkgs/gmp/rules       optional POSIX sh: do_configure / do_build / do_install
pkgs/gmp/patches/    optional, applied in order (series file)
```

`Build-System:` is `autotools`, `cmake`, `meson` or `custom`. The first three
supply defaults for the `do_*` functions; `rules` overrides any of them.

Binary packages list their files as globs relative to `DESTDIR`. Every file
must be claimed by exactly one package.

## State machine

```
needs-build -> building -> built
                  |  \-> failed   (retry only by explicit request)
                  \-> dep-wait    (a Build-Depends has no built version yet)
built -> needs-build            (ABI of a build-dependency changed, or recipe changed)
```

Every transition is appended to a history table.

## Bootstrap (honest statement of the trust root)

* **Stage 0 (now):** the build container's host tools (compiler front-ends,
  make, cmake, meson, python) come from Ubuntu. The cross toolchain
  (`x86_64-smechos-linux-gnu`) is built from source with crosstool-ng.
* **Stage 1:** glibc, kernel headers and the GCC runtime from that toolchain are
  imported as the first `.spkg` files; everything else is built by recipes.
* **Stage 2 (later):** rebuild the build tools themselves with smechbuild and
  switch the sandbox base to them, which removes Ubuntu from the build chain.

"Everything from source" is true for the target system at stage 1 and for the
whole chain only after stage 2.

## Not built yet

* Distributing builds across machines (the buildd loop is single-host; the DB
  and claim protocol are written so several builders can share one DB file
  later or a server DB).
* Signing the repository (reuse the existing Smech Labs archive keyring).
* Binary-package signing and the root key ceremony.
