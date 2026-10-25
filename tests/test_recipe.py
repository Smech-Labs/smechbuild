import os
import unittest
from smechbuild import recipe
from tests.helpers import write_recipe, tmpdir


class RecipeTests(unittest.TestCase):
    def test_loads_valid(self):
        with tmpdir() as t:
            d = write_recipe(t, "gmp", "6.3.0", build_depends="m4, perl (>= 5) | perl5",
                             packages={"libgmp": ["usr/lib/*.so.*"], "libgmp-dev": ["usr/include/*"]})
            r = recipe.load(d)
            self.assertEqual(r.full_version, "6.3.0-1")
            self.assertEqual(r.build_depends, ["m4", "perl (>= 5) | perl5"])
            self.assertEqual([b.name for b in r.packages], ["libgmp", "libgmp-dev"])
            self.assertEqual(r.sources[0].filename, "gmp-6.3.0.tar.xz")

    def test_rejects_bad_input(self):
        bad = {
            "dir name must equal Source": lambda t: os.rename(write_recipe(t, "a"), os.path.join(t, "b")),
        }
        for why, fn in bad.items():
            with self.subTest(why), tmpdir() as t:
                fn(t)
                with self.assertRaisesRegex(recipe.RecipeError, "directory name"):
                    recipe.load(os.path.join(t, "b"))

    def test_field_validation(self):
        with tmpdir() as t:
            d = write_recipe(t, "x", packages={"x": ["/etc/passwd"]})
            with self.assertRaisesRegex(recipe.RecipeError, "relative"):
                recipe.load(d)
        with tmpdir() as t:
            d = write_recipe(t, "x", build_system="make")
            with self.assertRaisesRegex(recipe.RecipeError, "Build-System"):
                recipe.load(d)
        with tmpdir() as t:
            d = write_recipe(t, "x")
            p = os.path.join(d, "control")
            text = open(p).read().replace("a" * 64, "zz")
            open(p, "w").write(text)
            with self.assertRaisesRegex(recipe.RecipeError, "sha256"):
                recipe.load(d)
        with tmpdir() as t:
            d = write_recipe(t, "x", build_system="custom")
            with self.assertRaisesRegex(recipe.RecipeError, "rules"):
                recipe.load(d)

    def test_duplicate_binary_across_sources(self):
        with tmpdir() as t:
            write_recipe(t, "a", packages={"shared": ["usr/a"]})
            write_recipe(t, "b", packages={"shared": ["usr/b"]})
            with self.assertRaisesRegex(recipe.RecipeError, "both a and b"):
                recipe.load_all(t)

    def test_digest_changes_with_rules_and_patches(self):
        with tmpdir() as t:
            d = write_recipe(t, "x")
            r = recipe.load(d)
            d0 = recipe.digest(r)
            open(os.path.join(d, "rules"), "w").write("do_build() { :; }\n")
            d1 = recipe.digest(r)
            os.makedirs(os.path.join(d, "patches"))
            open(os.path.join(d, "patches", "series"), "w").write("a.patch\n")
            open(os.path.join(d, "patches", "a.patch"), "w").write("x")
            d2 = recipe.digest(r)
            self.assertEqual(len({d0, d1, d2}), 3)


if __name__ == "__main__":
    unittest.main()
