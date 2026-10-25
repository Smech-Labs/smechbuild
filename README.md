# smechbuild

Builds SmechOS packages from source in a clean sandbox and keeps a queue of what
needs (re)building. Modeled on Debian's pipeline (sbuild, wanna-build, buildd,
`.buildinfo`, binNMUs); see `docs/DESIGN.md` for the mapping and the rules.

Written with AI assistance (Claude). Unit tests cover recipes, the graph and
planner, the state machine, packaging and the repo; the sandboxed build path was
run end to end on three packages only. Review it like any other new code.

Status: first slice. It builds real packages end to end (gmp, mpfr, mpc are the
proof), but the existing `spk-compile.py` pipeline still builds the actual OS
image. Recipes for the rest of the system do not exist yet.

## Quick start

```sh
cp smechbuild.toml.example smechbuild.toml    # edit paths
python3 -m smechbuild check                   # validate recipes + graph
python3 -m smechbuild bootstrap               # one-time: import the cross toolchain's sysroot
python3 -m smechbuild plan                    # what needs building, and why
python3 -m smechbuild buildd --once           # build everything that is ready
python3 -m smechbuild status                  # queue state
python3 -m smechbuild history gmp             # state transitions
```

Needs: Python 3.12+, rootless podman, `readelf`, and the `spk` binary (its
solver resolves Build-Depends; `spk solve --porcelain`, `SPK_ROOT`).

## Writing a recipe

`pkgs/<source>/control` (deb822) and optionally `rules` and `patches/`:

```
Source: gmp
Version: 6.3.0
Revision: 1
Build-System: autotools          # autotools | cmake | meson | custom
Build-Depends: libc-dev, libgcc-s1
Sources:
 <sha256> <size> https://gmplib.org/download/gmp/gmp-6.3.0.tar.xz
Configure-Args: --disable-static

Package: libgmp10
Files:
 usr/lib/libgmp.so.*

Package: libgmp-dev
Depends: libgmp10 (= ${binary:Version})
Files:
 usr/include/gmp.h
 usr/lib/libgmp.so
```

* The first source is unpacked (one leading directory stripped) into `/build/src`.
* `rules` may define `do_configure`, `do_build`, `do_install`; `NO_STRIP=1` skips stripping.
* Every installed file must be claimed by exactly one `Files` glob and every glob
  must match, or the build fails.
* `Depends` on shared libraries are generated from ELF `NEEDED`; a library no
  build-dependency provides is an error.

## What a build produces

`repo/pool/<name>_<version>.spkg`, `repo/<name>.spkg` (latest, what `spk`
downloads), `repo/index.txt`, `repo/index.meta`, and
`repo/buildinfo/<source>_<version>.json` recording every build-dependency's
version, hash and ABI key, the container image and the toolchain triplet.

## Tests

```sh
python3 -I -m unittest discover -s . -t .
```

## Known gaps

* Single host. The DB and claim protocol allow several builders, nothing
  distributes work yet.
* Repository and packages are not signed.
* Only the first `Sources` entry is unpacked; the rest are copied to `/build/extra`.
* `LIBDIR` is `/usr/lib`; reconcile with the multiarch layout in `SABI.md`
  before packaging anything that ships into the current image.
* Build tools inside the sandbox still come from the container image (stage 0,
  see `docs/DESIGN.md`).
* No separate Build-Depends for the build machine vs the target (cross
  `Build-Depends:native`), no build profiles to break dependency cycles.
