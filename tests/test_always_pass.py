from __future__ import annotations
import typing
import unittest


class TestAlwaysPass(unittest.TestCase):
    def test_pass(self) -> None:
        self.assertTrue(2 * 2 == 4, "2 * 2 should be 4")


class TestAlwaysPass2(unittest.TestCase):
    def test_pass(self) -> None:
        self.assertTrue(2 * 3 == 6, "2 * 3 should be 6")
