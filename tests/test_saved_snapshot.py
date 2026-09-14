"""`fdp snapshot` — building, showing and verifying a saved snapshot.

No device package needed: these exercise argument handling and the document,
not the origin. The `needs_d3d` guard in test_env_parity.py exists because
the conda build env has no device contributor, and a class that forgets it
reddens a build for a reason unrelated to what it tests.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fdp import saved_snapshot as ss

STAMP = "catalog_20260907T232802Z"


def a_doc(shots=((165920, 2), (165921, 3)), shared=(("efit01-0", 5),)):
    return {
        "schema": "fdp-snapshot/1",
        "catalog": STAMP,
        "store_root": "pelican://osg-htc.org:443/fdp-d3d",
        "created_at": "2026-09-14T20:00:00Z",
        "shots": [{"shot": s, "version": v, "dir_hash": "%032x" % s}
                  for s, v in shots],
        "shared": [{"shard": k, "version": v, "dir_hash": "a" * 32}
                   for k, v in shared],
    }


class TestParsingAShotList(unittest.TestCase):
    def test_a_comma_list(self):
        self.assertEqual(ss.parse_shots("165920,165921"), [165920, 165921])

    def test_whitespace_and_blanks_are_tolerated(self):
        self.assertEqual(ss.parse_shots(" 165920 , ,165921 "),
                         [165920, 165921])

    def test_a_range(self):
        self.assertEqual(ss.parse_shots("165920-165922"),
                         [165920, 165921, 165922])

    def test_an_at_file_because_650_shots_do_not_fit_on_a_command_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as fh:
            fh.write("165920\n165921\n\n# a comment\n165922\n")
            path = fh.name
        self.assertEqual(ss.parse_shots("@" + path),
                         [165920, 165921, 165922])

    def test_rubbish_is_refused_naming_the_offender(self):
        with self.assertRaises(SystemExit) as cm:
            ss.parse_shots("165920,banana")
        self.assertIn("banana", str(cm.exception))

    def test_a_missing_at_file_names_the_path(self):
        with self.assertRaises(SystemExit) as cm:
            ss.parse_shots("@/no/such/file.txt")
        self.assertIn("/no/such/file.txt", str(cm.exception))


class TestTreesBecomeShards(unittest.TestCase):
    """The mapping is toksearch's; fdp must not grow a second copy."""

    def test_one_tree_one_span(self):
        with mock.patch.object(ss, "_shard_key",
                               lambda t, s: "%s-%d" % (t, s // 1_000_000)):
            self.assertEqual(ss.shards_for(["efit01"], [165920, 165921]),
                             ["efit01-0"])

    def test_a_tree_across_a_boundary_is_two_shards(self):
        with mock.patch.object(ss, "_shard_key",
                               lambda t, s: "%s-%d" % (t, s // 1_000_000)):
            self.assertEqual(ss.shards_for(["efit01"], [165920, 1165920]),
                             ["efit01-0", "efit01-1"])

    def test_no_trees_is_no_shards(self):
        self.assertEqual(ss.shards_for([], [165920]), [])


class TestExtract(unittest.TestCase):
    def test_it_lifts_the_snapshot_out_of_an_inputs_file(self):
        inputs = {"source": {}, "signals": {}, "device": "d3d",
                  "archive_version": a_doc()}
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            json.dump(inputs, fh)
            path = fh.name
        self.assertEqual(ss.extract(path)["catalog"], STAMP)

    def test_an_unversioned_run_says_so_rather_than_writing_junk(self):
        inputs = {"archive_version": "unversioned"}
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            json.dump(inputs, fh)
            path = fh.name
        with self.assertRaises(SystemExit) as cm:
            ss.extract(path)
        self.assertIn("unversioned", str(cm.exception))


class TestShow(unittest.TestCase):
    def test_it_reports_the_token_the_catalog_and_the_counts(self):
        buf = io.StringIO()
        with mock.patch.object(ss, "_token", lambda d: "deadbeef"), \
             redirect_stdout(buf):
            ss.show(a_doc())
        out = buf.getvalue()
        self.assertIn("deadbeef", out)
        self.assertIn(STAMP, out)
        self.assertIn("2 shots", out)
        self.assertIn("1 shard", out)

    def test_it_says_what_it_did_not_check(self):
        # `show` proves the list is intact, not that the bytes match. Saying
        # so is the difference between a summary and a false assurance.
        #
        # Asserting only that "verify" appears was too weak: the pointer to
        # `fdp snapshot verify` also contains that word, so deleting the
        # caveat itself changed nothing observable.
        buf = io.StringIO()
        with mock.patch.object(ss, "_token", lambda d: "deadbeef"), \
             redirect_stdout(buf):
            ss.show(a_doc())
        out = buf.getvalue().lower()
        self.assertIn("bytes", out)      # the claim it does NOT make
        self.assertIn("verify", out)     # and where to get it

    def test_a_snapshot_naming_no_shards_is_told_what_that_costs(self):
        # Without shards the citation leans on its catalog for model trees,
        # and catalogs are pruned. Silence here would let someone publish a
        # citation with a shelf life they were never told about.
        buf = io.StringIO()
        with mock.patch.object(ss, "_token", lambda d: "deadbeef"), \
             redirect_stdout(buf):
            ss.show(a_doc(shared=()))
        self.assertIn("catalog", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
