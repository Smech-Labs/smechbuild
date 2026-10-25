import os
import tempfile

SHA = "a" * 64


def write_recipe(root, name, version="1.0", revision=1, build_depends="", packages=None,
                 build_system="autotools", extra_source=""):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    packages = packages or {name: ["usr/lib/*"]}
    text = (
        f"Source: {name}\nVersion: {version}\nRevision: {revision}\n"
        f"Build-System: {build_system}\n"
        + (f"Build-Depends: {build_depends}\n" if build_depends else "")
        + f"Sources:\n {SHA} 10 https://example.org/{name}-{version}.tar.xz\n"
        + extra_source
    )
    for pname, files in packages.items():
        text += f"\nPackage: {pname}\nDescription: {pname}\nFiles:\n" + "".join(f" {f}\n" for f in files)
    with open(os.path.join(d, "control"), "w") as f:
        f.write(text)
    return d


def tmpdir():
    return tempfile.TemporaryDirectory()
