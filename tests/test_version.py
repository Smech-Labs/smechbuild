import unittest
from smechbuild.version import compare


class VersionTests(unittest.TestCase):
    def test_order(self):
        cases = [
            ("1.0", "1.0", 0), ("1.0", "1.1", -1), ("1.10", "1.9", 1),
            ("1.0~rc1", "1.0", -1), ("1.0-2", "1.0-10", -1), ("1:0.5", "9.9", 1),
            ("6.11.2-1", "6.11.10-1", -1), ("1.0a", "1.0", 1), ("1.0+git", "1.0", 1),
            ("2.0", "2.0-1", -1),
        ]
        for a, b, want in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(compare(a, b), want)
                self.assertEqual(compare(b, a), -want)


if __name__ == "__main__":
    unittest.main()
