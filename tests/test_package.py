import os
import unittest

from smechbuild import package, recipe, repo, spkg
from smechbuild.recipe import BinaryPackage
from tests.helpers import tmpdir


def tree(root, files, links=()):
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    for rel, target in links:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(target, p)


class SplitTests(unittest.TestCase):
    def test_split_claims_everything_once(self):
        with tmpdir() as t:
            tree(t, {"usr/lib/libx.so.1": "a", "usr/include/x.h": "b", "usr/share/doc/x/README": "c"},
                 links=[("usr/lib/libx.so", "libx.so.1")])
            got = package.split(t, [
                BinaryPackage("libx1", files=["usr/lib/libx.so.*"]),
                BinaryPackage("libx-dev", files=["usr/include/*", "usr/lib/libx.so", "usr/share/doc"]),
            ])
            self.assertEqual(got["libx1"], ["usr/lib/libx.so.1"])
            self.assertEqual(sorted(got["libx-dev"]),
                             ["usr/include/x.h", "usr/lib/libx.so", "usr/share/doc/x/README"])

    def test_unclaimed_files_fail(self):
        with tmpdir() as t:
            tree(t, {"usr/lib/a": "1", "usr/bin/oops": "2"})
            with self.assertRaisesRegex(package.PackagingError, "(?s)belong to no package.*usr/bin/oops"):
                package.split(t, [BinaryPackage("a", files=["usr/lib/*"])])

    def test_overlap_and_dead_glob_fail(self):
        with tmpdir() as t:
            tree(t, {"usr/lib/a": "1"})
            with self.assertRaisesRegex(package.PackagingError, "claimed by both"):
                package.split(t, [BinaryPackage("p", files=["usr/lib/*"]), BinaryPackage("q", files=["usr/lib/a"])])
            with self.assertRaisesRegex(package.PackagingError, "matched nothing"):
                package.split(t, [BinaryPackage("p", files=["usr/lib/*", "usr/nothing/*"])])

    def test_merge_depends(self):
        out = package.merge_depends("libgmp10 (= ${binary:Version}), foo", {"libgmp10": (None, True),
                                    "libc": ("2.41-1", False), "bar": (None, False)}, "self", "1.0-1")
        self.assertEqual(out, "libgmp10 (= 1.0-1), foo, bar, libc (>= 2.41-1)")


class SpkgTests(unittest.TestCase):
    def make(self, t, name="x", content="hello"):
        pd = os.path.join(t, "payload")
        tree(pd, {"usr/share/x/data": content}, links=[("usr/share/x/link", "data")])
        out = os.path.join(t, f"{name}.spkg")
        spkg.write(out, {"name": name, "version": "1.0-1", "depends": "a (>= 1)"}, pd, hooks={"postinst": "#!/bin/sh\n"})
        return out

    def test_round_trip_and_checksums(self):
        with tmpdir() as t:
            p = self.make(t)
            c = spkg.read_control(p)
            self.assertEqual((c["name"], c["version"], c["depends"]), ("x", "1.0-1", "a (>= 1)"))
            dest = os.path.join(t, "out")
            spkg.extract_data(p, dest)
            self.assertEqual(open(os.path.join(dest, "usr/share/x/data")).read(), "hello")
            self.assertEqual(os.readlink(os.path.join(dest, "usr/share/x/link")), "data")
            self.assertIn(("usr/share/x/data", False), spkg.list_data(p))

    def test_build_is_deterministic(self):
        with tmpdir() as a, tmpdir() as b, tmpdir() as c:
            ha, hb = spkg.file_sha256(self.make(a)), spkg.file_sha256(self.make(b))
            self.assertEqual(ha, hb)
            self.assertNotEqual(spkg.file_sha256(self.make(c, content="other")), ha)


class RepoTests(unittest.TestCase):
    def test_publish_indexes_latest_and_all_versions(self):
        with tmpdir() as t:
            repo_dir = os.path.join(t, "repo")
            for ver in ("1.0-1", "1.0-2", "1.0-10"):
                pd = os.path.join(t, "pd" + ver)
                tree(pd, {"usr/share/x": ver})
                out = os.path.join(t, f"x_{ver}.spkg")
                spkg.write(out, {"name": "x", "version": ver, "depends": "", "provides": "",
                                 "conflicts": "", "source": "x", "abi": "k" + ver}, pd)
                repo.publish(repo_dir, [out], {"source": "x", "version": ver})
            self.assertEqual(open(os.path.join(repo_dir, "index.txt")).read(), "x 1.0-10\n")
            meta = open(os.path.join(repo_dir, "index.meta")).read()
            self.assertEqual(meta.count("name: x\n"), 3)
            self.assertEqual(spkg.read_control(os.path.join(repo_dir, "x.spkg"))["version"], "1.0-10")
            self.assertEqual(repo.load_repo_state(repo_dir)["x"]["abi"], "k1.0-10")
            self.assertEqual(repo.load_buildinfos(repo_dir)["x"]["version"], "1.0-10")


if __name__ == "__main__":
    unittest.main()
