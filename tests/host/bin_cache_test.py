#!/usr/bin/env python3
"""Unit tests for fkst-framework BIN cache path helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def load_bin_cache():
    path = Path(__file__).resolve().parents[2] / "host" / "bin_cache.py"
    spec = importlib.util.spec_from_file_location("bin_cache", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load bin_cache.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bin_cache = load_bin_cache()


class SubstrateBinCachePathTest(unittest.TestCase):
    def test_path_contract_uses_separate_encoded_components(self) -> None:
        path = bin_cache.substrate_bin_cache_path(
            "/var/cache/fkst",
            "ChronoAIProject",
            "fkst-packages",
            "refs/heads/dev",
        )

        self.assertEqual(
            path.as_posix(),
            "/var/cache/fkst/fkst-engine-bin/v1/"
            "ChronoAIProject/fkst-packages/refs%2Fheads%2Fdev/"
            "target/debug/fkst-framework",
        )

    def test_distinct_triples_do_not_collide_through_separator_replacement(self) -> None:
        first = bin_cache.substrate_bin_cache_path("/cache", "a/b", "c", "d")
        second = bin_cache.substrate_bin_cache_path("/cache", "a", "b/c", "d")
        third = bin_cache.substrate_bin_cache_path("/cache", "a", "b", "c/d")

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertNotEqual(second, third)
        self.assertIn("/a%2Fb/c/d/", first.as_posix())
        self.assertIn("/a/b%2Fc/d/", second.as_posix())
        self.assertIn("/a/b/c%2Fd/", third.as_posix())

    def test_space_dot_segments_and_special_characters_are_encoded_safely(self) -> None:
        path = bin_cache.substrate_bin_cache_path(
            "/cache",
            ".",
            "..",
            "feature/a b?c#d%20",
        )

        self.assertEqual(
            path.as_posix(),
            "/cache/fkst-engine-bin/v1/%2E/%2E%2E/"
            "feature%2Fa%20b%3Fc%23d%2520/target/debug/fkst-framework",
        )

    def test_empty_or_nul_components_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            bin_cache.substrate_bin_cache_path("/cache", "", "repo", "dev")
        with self.assertRaises(ValueError):
            bin_cache.substrate_bin_cache_path("/cache", "owner", "repo\x00x", "dev")

    def test_non_string_components_fail_closed(self) -> None:
        with self.assertRaises(TypeError):
            bin_cache.substrate_bin_cache_path("/cache", "owner", "repo", 123)


if __name__ == "__main__":
    unittest.main()
