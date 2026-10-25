#!/bin/sh
# Runs INSIDE the sandbox. Mounts: /build/src (rw), /build/obj (rw), /destdir (rw),
# /sysroot (ro, build-dependencies), /recipe (ro), /toolchain (ro, optional).
# Environment: BUILD_SYSTEM TRIPLET JOBS PREFIX LIBDIR CONFIGURE_ARGS
set -eu

SRC=/build/src
OBJ=/build/obj
DESTDIR=/destdir
SYSROOT=/sysroot
export SRC OBJ DESTDIR SYSROOT PREFIX LIBDIR JOBS TRIPLET
: "${PREFIX:=/usr}" "${LIBDIR:=/usr/lib}" "${JOBS:=2}" "${TRIPLET:=}" "${CONFIGURE_ARGS:=}"

# Reproducibility: fixed time and locale, no inherited host state.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1}" LC_ALL=C TZ=UTC

BUILD_MACHINE="$(cc -dumpmachine)"

if [ -n "$TRIPLET" ]; then
    export PATH="/toolchain/bin:$PATH"
    export CC="$TRIPLET-gcc --sysroot=$SYSROOT"
    export CXX="$TRIPLET-g++ --sysroot=$SYSROOT"
    export CPP="$TRIPLET-gcc --sysroot=$SYSROOT -E"
    export AR="$TRIPLET-ar" RANLIB="$TRIPLET-ranlib" STRIP="$TRIPLET-strip"
    export LD="$TRIPLET-ld" NM="$TRIPLET-nm" OBJCOPY="$TRIPLET-objcopy" READELF="$TRIPLET-readelf"
    export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
    export PKG_CONFIG_LIBDIR="$SYSROOT$LIBDIR/pkgconfig:$SYSROOT$PREFIX/share/pkgconfig"
    unset PKG_CONFIG_PATH || true
    HOST_ARG="--host=$TRIPLET --build=$BUILD_MACHINE"
else
    export PKG_CONFIG_PATH="$SYSROOT$LIBDIR/pkgconfig:$SYSROOT$PREFIX/share/pkgconfig"
    export CPPFLAGS="-I$SYSROOT$PREFIX/include ${CPPFLAGS:-}"
    export LDFLAGS="-L$SYSROOT$LIBDIR ${LDFLAGS:-}"
    HOST_ARG=""
fi

# ---- default steps per build system; recipe `rules` may override any -----
autotools_configure() {
    # shellcheck disable=SC2086
    "$SRC/configure" --prefix="$PREFIX" --libdir="$LIBDIR" --sysconfdir=/etc \
        --localstatedir=/var $HOST_ARG $CONFIGURE_ARGS
}
autotools_build()   { make -j"$JOBS"; }
autotools_install() { make DESTDIR="$DESTDIR" install; }

cmake_configure() {
    # shellcheck disable=SC2086
    cmake -S "$SRC" -B "$OBJ" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR="${LIBDIR#"$PREFIX"/}" \
        ${TRIPLET:+-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=x86_64 \
          -DCMAKE_C_COMPILER="$TRIPLET-gcc" -DCMAKE_CXX_COMPILER="$TRIPLET-g++" \
          -DCMAKE_SYSROOT="$SYSROOT" -DCMAKE_FIND_ROOT_PATH="$SYSROOT" \
          -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY \
          -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY} \
        $CONFIGURE_ARGS
}
cmake_build()   { cmake --build "$OBJ" -j "$JOBS"; }
cmake_install() { DESTDIR="$DESTDIR" cmake --install "$OBJ"; }

meson_configure() {
    # shellcheck disable=SC2086
    meson setup "$OBJ" "$SRC" --prefix="$PREFIX" --libdir="${LIBDIR#"$PREFIX"/}" \
        --buildtype=release ${MESON_CROSS_FILE:+--cross-file "$MESON_CROSS_FILE"} $CONFIGURE_ARGS
}
meson_build()   { meson compile -C "$OBJ" -j "$JOBS"; }
meson_install() { DESTDIR="$DESTDIR" meson install -C "$OBJ" --no-rebuild; }

custom_configure() { :; }
custom_build()     { :; }
custom_install()   { echo "Build-System: custom needs do_install in rules" >&2; return 1; }

do_configure() { "${BUILD_SYSTEM}_configure"; }
do_build()     { "${BUILD_SYSTEM}_build"; }
do_install()   { "${BUILD_SYSTEM}_install"; }

[ -f /recipe/rules ] && . /recipe/rules

step() { echo "=== smechbuild: $1 ($(date -u +%H:%M:%S)) ==="; }

cd "$SRC"
if [ -f /recipe/patches/series ]; then
    step patch
    while IFS= read -r p; do
        case "$p" in ''|'#'*) continue ;; esac
        echo "applying $p"
        patch -p1 --batch --forward < "/recipe/patches/$p"
    done < /recipe/patches/series
fi

mkdir -p "$OBJ"
step configure; cd "$OBJ"; do_configure
step build;     cd "$OBJ"; do_build
step install;   cd "$OBJ"; do_install
# Libtool archives hard-code absolute build-time paths and break later
# cross builds (they point at the build machine's /usr/lib). Debian drops them
# too. A recipe that really needs them can set KEEP_LA=1.
if [ -z "${KEEP_LA:-}" ]; then
    step drop-la
    find "$DESTDIR" -name '*.la' -type f -exec rm -f {} +
fi

if [ -z "${NO_STRIP:-}" ]; then
    step strip
    S="${STRIP:-strip}"
    elf_list="$OBJ/.elf-files"
    find "$DESTDIR" -type f > "$elf_list"
    while IFS= read -r f; do
        [ "$(head -c4 "$f" 2>/dev/null | od -An -c | tr -d ' ')" = "177ELF" ] || continue
        case "$f" in
            *.so|*.so.*) "$S" --strip-unneeded "$f" ;;
            *)           "$S" --strip-all "$f" ;;
        esac
    done < "$elf_list"
    rm -f "$elf_list"
fi
step done
