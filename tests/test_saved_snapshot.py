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


class TestNameLists(unittest.TestCase):
    """`--tree bci,efit01` is what a user types after `--shot 1,2,3`.

    Before this, --tree only repeated, so a comma produced one tree called
    "bci,efit01" and the run failed naming a shard called `bci,efit01-0` --
    which reads as a hole in the store rather than a typo.
    """

    def test_a_comma_separates_names(self):
        self.assertEqual(ss.parse_names(["bci,efit01"]), ["bci", "efit01"])

    def test_repeating_the_flag_still_works(self):
        self.assertEqual(ss.parse_names(["bci", "efit01"]), ["bci", "efit01"])

    def test_the_two_forms_mix(self):
        self.assertEqual(ss.parse_names(["bci,efit01", "transp"]),
                         ["bci", "efit01", "transp"])

    def test_blanks_and_spacing_are_forgiven(self):
        self.assertEqual(ss.parse_names([" bci , ,efit01 "]),
                         ["bci", "efit01"])

    def test_order_is_kept_and_repeats_dropped(self):
        # Order is the user's, not sorted: the message that reports what was
        # recorded should read back the way they typed it. build_snapshot
        # sorts the shards itself, so nothing downstream depends on this.
        self.assertEqual(ss.parse_names(["efit01,bci,efit01"]),
                         ["efit01", "bci"])

    def test_nothing_given_is_nothing(self):
        self.assertEqual(ss.parse_names(None), [])
        self.assertEqual(ss.parse_names([]), [])


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


class TestItRestartsWithTheComposedEnvironment(unittest.TestCase):
    """Some subcommands read the store in THIS process, and that cannot work
    when the environment is composed in Python: libXrdCl and libfdpio read
    XRD_PLUGINCONFDIR and BEARER_TOKEN in their static initialisers, long
    before `setup_environment` assigns to os.environ. `fdp catalog` reported
    "no catalog found" against a healthy store because of it."""

    def _args(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(**kw)

    def test_a_store_reading_subcommand_restarts(self):
        from fdp import cli
        with mock.patch.object(cli.os, "execve") as execve, \
             mock.patch.dict(cli.os.environ, {}, clear=False):
            cli.os.environ.pop(cli._ENV_APPLIED, None)
            cli._reexec_with_composed_env(self._args(reads_store=True))
        self.assertTrue(execve.called)

    def test_a_subcommand_that_reads_no_store_does_not(self):
        from fdp import cli
        with mock.patch.object(cli.os, "execve") as execve:
            cli._reexec_with_composed_env(self._args(reads_store=False))
        self.assertFalse(execve.called)

    def test_the_child_does_not_restart_again(self):
        # Marked rather than counted: a failure to apply the environment
        # must not become an exec loop.
        from fdp import cli
        with mock.patch.object(cli.os, "execve") as execve, \
             mock.patch.dict(cli.os.environ, {cli._ENV_APPLIED: "1"}):
            cli._reexec_with_composed_env(self._args(reads_store=True))
        self.assertFalse(execve.called)

    def test_the_marked_subcommands_are_the_ones_that_read_the_store(self):
        from fdp.cli import build_parser
        p = build_parser()
        for argv in (["catalog"], ["snapshot", "save", "--shot", "1", "-o", "f"],
                     ["snapshot", "verify", "f"]):
            with self.subTest(argv=argv):
                self.assertTrue(getattr(p.parse_args(argv), "reads_store", False))
        for argv in (["snapshot", "show", "f"], ["ls", "/"],
                     ["snapshot", "extract", "i", "-o", "f"]):
            with self.subTest(argv=argv):
                self.assertFalse(getattr(p.parse_args(argv), "reads_store", False))


# ---------------------------------------------------------------------------
# fdp-snapshot/2: the d3drdb snapshot a run read
# ---------------------------------------------------------------------------

import sys
import types
from types import SimpleNamespace

SQL_ID = "d3drdb_20261006T135944Z"


def a_v2_doc(**kw):
    doc = a_doc(**kw)
    doc["schema"] = "fdp-snapshot/2"
    doc["sql_snapshots"] = {"d3drdb": SQL_ID}
    return doc


def a_sql_locator(name="d3drdb"):
    return SimpleNamespace(kind="sql_snapshot", name=name,
                           base_url="pelican://osg-htc.org:443/fdp-d3d/x",
                           id_pattern="d3drdb_*", auth=None)


class SnapshotError(Exception):
    pass


def fake_toksearch(resolve=None, verify_files=None):
    """The toksearch >= 2.19.0 client, as modules in sys.modules.

    The dev env carries an older toksearch, and the conda test env may too:
    the lazy imports must be served from here, not from whatever is there.
    """
    snap = types.ModuleType("toksearch.sql.snapshot")
    snap.SnapshotError = SnapshotError
    snap.resolve = resolve or mock.Mock(return_value=SQL_ID)
    snap.verify_files = verify_files or mock.Mock(
        return_value=SimpleNamespace(checked=4, total=4, failures=[]))
    pkg = types.ModuleType("toksearch")
    pkg.__path__ = []
    sql = types.ModuleType("toksearch.sql")
    sql.__path__ = []
    pkg.sql = sql
    sql.snapshot = snap
    return {"toksearch": pkg, "toksearch.sql": sql,
            "toksearch.sql.snapshot": snap}


def fake_ptdata(doc):
    pt = mock.Mock()
    pt.SCHEMAS = ("fdp-snapshot/1", "fdp-snapshot/2")
    pt.build_snapshot.return_value = doc
    pt.snapshot_token.return_value = "deadbeef"
    pt.verify_snapshot.return_value = SimpleNamespace(
        ok=True, failures=[], checked=2, total=2, sampled=False)
    return pt


def write_json(doc):
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as fh:
        json.dump(doc, fh)
        return fh.name


class TestSchemas(unittest.TestCase):
    def test_both_schemas_are_named(self):
        self.assertEqual(ss.SCHEMAS, ("fdp-snapshot/1", "fdp-snapshot/2"))

    def test_load_accepts_v1_and_v2(self):
        self.assertEqual(ss.load(write_json(a_doc()))["schema"],
                         "fdp-snapshot/1")
        self.assertEqual(ss.load(write_json(a_v2_doc()))["sql_snapshots"],
                         {"d3drdb": SQL_ID})

    def test_load_refuses_v3_by_name(self):
        doc = a_doc()
        doc["schema"] = "fdp-snapshot/3"
        with self.assertRaises(SystemExit) as cm:
            ss.load(write_json(doc))
        self.assertIn("fdp-snapshot/3", str(cm.exception))
        self.assertIn("fdp-snapshot/2", str(cm.exception))


class TestSqlLocators(unittest.TestCase):
    def test_the_sql_snapshot_locators_of_the_active_devices(self):
        other = SimpleNamespace(kind="mds_tree", name="main")
        handle = SimpleNamespace(schema=SimpleNamespace(
            name="d3d", locators=[other, a_sql_locator()]))
        with mock.patch("fdp.devices.active_handles",
                        return_value=[handle]) as ah:
            locs = ss.sql_locators("d3d")
        ah.assert_called_once_with("d3d")
        self.assertEqual([l.name for l in locs], ["d3drdb"])

    def test_a_device_without_one_has_none(self):
        handle = SimpleNamespace(schema=SimpleNamespace(
            name="mast", locators=[SimpleNamespace(kind="zarr_store",
                                                   name="main")]))
        with mock.patch("fdp.devices.active_handles", return_value=[handle]):
            self.assertEqual(ss.sql_locators(None), [])


class _Cli(unittest.TestCase):
    def run_cli(self, args, ptdata, locators=(), toksearch=None):
        from fdp import cli
        out, err = io.StringIO(), io.StringIO()
        from contextlib import redirect_stderr
        with mock.patch.object(ss, "_ptdata", lambda: ptdata), \
             mock.patch.object(ss, "sql_locators",
                               lambda device: list(locators)), \
             mock.patch.object(cli.catalog_mod, "resolve_flag",
                               lambda flag, root: STAMP), \
             mock.patch.dict(cli.os.environ,
                             {"FDP_STORE_ROOT": "pelican://x/fdp-d3d"}), \
             mock.patch.dict(sys.modules, toksearch or fake_toksearch()), \
             redirect_stdout(out), redirect_stderr(err):
            code = 0
            try:
                cli.do_snapshot(args)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()


def save_args(output):
    return SimpleNamespace(snapshot_command="save", shot="165920,165921",
                           catalog=None, shard=None, tree=None,
                           output=output, device=None)


class TestSave(_Cli):
    def test_a_device_with_a_sql_snapshot_locator_writes_v2(self):
        out_path = tempfile.mktemp(suffix=".json")
        pt = fake_ptdata(a_v2_doc())
        ts = fake_toksearch()
        code, out, _ = self.run_cli(save_args(out_path), pt,
                                    locators=[a_sql_locator()], toksearch=ts)
        self.assertFalse(code)
        kw = pt.build_snapshot.call_args.kwargs
        self.assertEqual(kw["sql_snapshots"], {"d3drdb": SQL_ID})
        loc = ts["toksearch.sql.snapshot"].resolve.call_args
        self.assertEqual(loc.args[0].name, "d3drdb")
        # The token is resolve's business (public API), not fdp's.
        self.assertEqual(loc.args[1:], ())
        self.assertEqual(loc.kwargs, {})
        self.assertIn("d3drdb  " + SQL_ID, out)
        with open(out_path) as fh:
            self.assertEqual(json.load(fh)["schema"], "fdp-snapshot/2")

    def test_no_locator_writes_v1_without_asking_toksearch(self):
        out_path = tempfile.mktemp(suffix=".json")
        pt = fake_ptdata(a_doc())
        ts = fake_toksearch()
        code, out, _ = self.run_cli(save_args(out_path), pt, toksearch=ts)
        self.assertFalse(code)
        self.assertIsNone(
            pt.build_snapshot.call_args.kwargs.get("sql_snapshots"))
        self.assertFalse(ts["toksearch.sql.snapshot"].resolve.called)
        self.assertNotIn("d3drdb", out)

    def test_a_declared_locator_that_cannot_resolve_writes_nothing(self):
        # A snapshot that cannot name what a run would read must not be
        # written without it: silently writing /1 would cite a run whose
        # shot metadata is unrecorded.
        out_path = tempfile.mktemp(suffix=".json")
        pt = fake_ptdata(a_v2_doc())
        ts = fake_toksearch(resolve=mock.Mock(
            side_effect=SnapshotError("no snapshot matching 'd3drdb_*'")))
        code, _, _ = self.run_cli(save_args(out_path), pt,
                                  locators=[a_sql_locator()], toksearch=ts)
        self.assertIn("no snapshot matching", str(code))
        self.assertFalse(pt.build_snapshot.called)
        self.assertFalse(Path(out_path).exists())

    def test_a_missing_toksearch_names_the_version(self):
        out_path = tempfile.mktemp(suffix=".json")
        pt = fake_ptdata(a_v2_doc())
        code, _, _ = self.run_cli(save_args(out_path), pt,
                                  locators=[a_sql_locator()],
                                  toksearch={"toksearch.sql.snapshot": None})
        self.assertIn("toksearch >= 2.19.0", str(code))
        self.assertFalse(Path(out_path).exists())

    def test_a_ptdata_that_cannot_write_v2_is_named(self):
        out_path = tempfile.mktemp(suffix=".json")
        pt = fake_ptdata(a_v2_doc())
        pt.SCHEMAS = ("fdp-snapshot/1",)
        code, _, _ = self.run_cli(save_args(out_path), pt,
                                  locators=[a_sql_locator()])
        self.assertIn("ptdata >= 2.11.3", str(code))
        self.assertFalse(pt.build_snapshot.called)


class TestShowV2(unittest.TestCase):
    def test_it_prints_each_sql_snapshot(self):
        buf = io.StringIO()
        with mock.patch.object(ss, "_token", lambda d: "deadbeef"), \
             redirect_stdout(buf):
            ss.show(a_v2_doc())
        self.assertIn("d3drdb  " + SQL_ID, buf.getvalue())

    def test_v1_prints_none(self):
        buf = io.StringIO()
        with mock.patch.object(ss, "_token", lambda d: "deadbeef"), \
             redirect_stdout(buf):
            ss.show(a_doc())
        self.assertNotIn("d3drdb", buf.getvalue())


class TestExtractV2(_Cli):
    def test_a_v2_archive_version_is_written_through(self):
        inputs = write_json({"archive_version": a_v2_doc()})
        out_path = tempfile.mktemp(suffix=".json")
        args = SimpleNamespace(snapshot_command="extract", path=inputs,
                               output=out_path)
        with mock.patch.object(ss, "_token", lambda d: "deadbeef"):
            code, _, _ = self.run_cli(args, fake_ptdata(None))
        self.assertFalse(code)
        written = ss.load(out_path)
        self.assertEqual(written["schema"], "fdp-snapshot/2")
        self.assertEqual(written["sql_snapshots"], {"d3drdb": SQL_ID})

    def test_an_unknown_schema_is_refused(self):
        doc = a_v2_doc()
        doc["schema"] = "fdp-snapshot/3"
        with self.assertRaises(SystemExit) as cm:
            ss.extract(write_json({"archive_version": doc}))
        self.assertIn("fdp-snapshot/3", str(cm.exception))


def verify_args(path, sample=None):
    return SimpleNamespace(snapshot_command="verify", path=path,
                           sample=sample, device=None)


class TestVerifyV2(_Cli):
    def test_it_checks_the_parquet_bytes(self):
        path = write_json(a_v2_doc())
        ts = fake_toksearch()
        code, out, _ = self.run_cli(verify_args(path, sample=3),
                                    fake_ptdata(None),
                                    locators=[a_sql_locator()], toksearch=ts)
        self.assertFalse(code)
        vf = ts["toksearch.sql.snapshot"].verify_files
        vf.assert_called_once()
        self.assertEqual(vf.call_args.args[0].name, "d3drdb")
        self.assertEqual(vf.call_args.args[1], SQL_ID)
        self.assertEqual(vf.call_args.kwargs.get("sample"), 3)
        self.assertIn(SQL_ID, out)

    def test_a_parquet_mismatch_fails_naming_the_file(self):
        path = write_json(a_v2_doc())
        ts = fake_toksearch(verify_files=mock.Mock(
            return_value=SimpleNamespace(
                checked=4, total=4,
                failures=[("shots/part-0.parquet", "aa", "bb")])))
        code, _, err = self.run_cli(verify_args(path), fake_ptdata(None),
                                    locators=[a_sql_locator()], toksearch=ts)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  parquet shots/part-0.parquet: expected aa got bb",
                      err)
        self.assertIn("FAILED", err)

    def test_a_v2_file_on_a_device_without_the_locator_is_an_error(self):
        path = write_json(a_v2_doc())
        code, _, _ = self.run_cli(verify_args(path), fake_ptdata(None))
        self.assertIn("d3drdb", str(code))
        self.assertNotIn(code, (0, None))

    def test_v1_does_not_touch_toksearch(self):
        path = write_json(a_doc())
        ts = fake_toksearch()
        code, _, _ = self.run_cli(verify_args(path), fake_ptdata(None),
                                  toksearch=ts)
        self.assertFalse(code)
        self.assertFalse(ts["toksearch.sql.snapshot"].verify_files.called)
