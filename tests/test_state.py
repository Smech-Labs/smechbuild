import os
import unittest
from smechbuild import graph, recipe, state
from tests.helpers import write_recipe, tmpdir


class StateTests(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir()
        write_recipe(self.t.name, "a", packages={"pa": ["x"]})
        write_recipe(self.t.name, "b", build_depends="pa", packages={"pb": ["x"]})
        self.rs = recipe.load_all(self.t.name)
        self.dg = {n: recipe.digest(r) for n, r in self.rs.items()}
        self.st = state.State(os.path.join(self.t.name, "db"))

    def tearDown(self):
        self.st.close()
        self.t.cleanup()

    def sync(self, infos=None, repo=None):
        self.st.sync(self.rs, graph.plan(self.rs, infos or {}, repo or {}), self.dg)

    def test_claim_is_exclusive_and_ordered(self):
        self.sync()
        self.assertEqual(self.st.get("b")["state"], "dep-wait")
        self.assertEqual(self.st.claim("w1"), "a")
        self.assertIsNone(self.st.claim("w2"))  # b is dep-wait, a is taken
        self.st.finish("a", True)
        self.assertEqual(self.st.get("a")["state"], "built")

    def test_finish_requires_building(self):
        self.sync()
        with self.assertRaises(RuntimeError):
            self.st.finish("a", True)

    def test_failed_is_sticky_until_recipe_changes(self):
        self.sync()
        self.st.claim("w")
        self.st.finish("a", False, "boom")
        self.sync()
        self.assertEqual(self.st.get("a")["state"], "failed")
        self.dg["a"] = "changed"
        self.sync()
        self.assertEqual(self.st.get("a")["state"], "needs-build")

    def test_retry_and_history(self):
        self.sync()
        self.st.claim("w")
        self.st.finish("a", False, "boom")
        self.st.retry("a")
        notes = [(h["old"], h["new"]) for h in self.st.history("a")]
        self.assertEqual(notes[-3:], [("needs-build", "building"), ("building", "failed"), ("failed", "needs-build")])
        with self.assertRaises(RuntimeError):
            self.st.retry("b")

    def test_reap_abandoned_build(self):
        self.sync()
        self.st.claim("w")
        self.assertEqual(self.st.reap(3600), [])
        self.assertEqual(self.st.reap(-1), ["a"])
        self.assertEqual(self.st.get("a")["state"], "needs-build")

    def test_sync_does_not_touch_running_build(self):
        self.sync()
        self.st.claim("w")
        self.sync()
        self.assertEqual(self.st.get("a")["state"], "building")


if __name__ == "__main__":
    unittest.main()
