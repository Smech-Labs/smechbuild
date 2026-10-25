import unittest
from smechbuild import graph, recipe
from tests.helpers import write_recipe, tmpdir


def bi(r, deps=()):
    return {"version": r.full_version, "recipe_digest": recipe.digest(r),
            "build_depends": [{"name": n, "abi": a} for n, a in deps]}


class GraphTests(unittest.TestCase):
    def make(self, t):
        write_recipe(t, "glibc", packages={"libc": ["usr/lib/libc*"]})
        write_recipe(t, "qt", build_depends="libc", packages={"qt6-base": ["usr/lib/libQt6*"]})
        write_recipe(t, "kf6", build_depends="qt6-base (>= 6) | qt5, cmake", packages={"kf6-core": ["usr/lib/*"]})
        return recipe.load_all(t)

    def test_edges_order_and_external(self):
        with tmpdir() as t:
            rs = self.make(t)
            edges, ext = graph.build_edges(rs)
            self.assertEqual(edges["kf6"], {"qt"})
            self.assertEqual(ext["kf6"], ["cmake"])
            self.assertEqual(graph.toposort(edges), ["glibc", "qt", "kf6"])
            self.assertEqual(graph.reverse_closure(edges, ["glibc"]), {"qt", "kf6"})

    def test_cycle_is_reported(self):
        with tmpdir() as t:
            write_recipe(t, "a", build_depends="pb", packages={"pa": ["x"]})
            write_recipe(t, "b", build_depends="pa", packages={"pb": ["x"]})
            rs = recipe.load_all(t)
            edges, _ = graph.build_edges(rs)
            with self.assertRaises(graph.CycleError) as cm:
                graph.toposort(edges)
            self.assertEqual(cm.exception.cycle[0], cm.exception.cycle[-1])
            v = graph.plan(rs, {}, {})
            self.assertTrue(all(x.state == "blocked" for x in v.values()))

    def test_fresh_tree_waits_in_order(self):
        with tmpdir() as t:
            rs = self.make(t)
            v = graph.plan(rs, {}, {})
            self.assertEqual(v["glibc"].state, "needs-build")
            self.assertEqual(v["qt"].state, "dep-wait")
            self.assertEqual(v["kf6"].state, "dep-wait")

    def test_abi_change_cascades_but_version_only_does_not(self):
        with tmpdir() as t:
            rs = self.make(t)
            infos = {
                "glibc": bi(rs["glibc"]),
                "qt": bi(rs["qt"], [("libc", "abi1")]),
                "kf6": bi(rs["kf6"], [("qt6-base", "qtabi1")]),
            }
            repo = {"libc": {"abi": "abi1"}, "qt6-base": {"abi": "qtabi1"}, "kf6-core": {"abi": "k"}}
            v = graph.plan(rs, infos, repo)
            self.assertTrue(all(x.state == "up-to-date" for x in v.values()))
            # new Qt version, same ABI: nothing downstream rebuilds
            repo["qt6-base"] = {"abi": "qtabi1", "version": "9"}
            self.assertEqual(graph.plan(rs, infos, repo)["kf6"].state, "up-to-date")
            # Qt ABI changes: kf6 must rebuild
            repo["qt6-base"] = {"abi": "qtabi2"}
            v = graph.plan(rs, infos, repo)
            self.assertEqual(v["kf6"].state, "needs-build")
            self.assertIn("ABI of qt6-base changed", v["kf6"].reasons)

    def test_new_version_of_dependency_makes_dependents_wait(self):
        with tmpdir() as t:
            rs = self.make(t)
            infos = {
                "glibc": bi(rs["glibc"]),
                "qt": bi(rs["qt"], [("libc", "abi1")]),
                "kf6": bi(rs["kf6"], [("qt6-base", "q1")]),
            }
            repo = {"libc": {"abi": "abi2"}, "qt6-base": {"abi": "q1"}, "kf6-core": {"abi": "k"}}
            v = graph.plan(rs, infos, repo)
            self.assertEqual(v["glibc"].state, "up-to-date")
            self.assertEqual(v["qt"].state, "needs-build")  # libc ABI differs from what it saw
            self.assertEqual(v["kf6"].state, "up-to-date")  # decided once qt is rebuilt


if __name__ == "__main__":
    unittest.main()
