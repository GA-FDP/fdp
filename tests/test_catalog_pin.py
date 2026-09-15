"""`--catalog`: resolving the flag, and what reaches the environment.

No device package needed: these exercise the flag, not the origin. The
`needs_d3d` guard in test_env_parity.py exists because the conda build
environment has no device contributor at all, and a class that forgets it
reddens a build for a reason unrelated to what it tests.
"""

import contextlib
import io
import unittest
from unittest import mock

from fdp import catalog_pin as catalog

STAMP = "catalog_20260907T232802Z"
NEWER = "catalog_29991231T235959Z"
ROOT = "pelican://osg-htc.org:443/fdp-d3d"


class TestResolvingTheFlag(unittest.TestCase):
    def test_a_concrete_stamp_passes_through(self):
        with mock.patch.object(catalog, "_newest") as newest:
            self.assertEqual(catalog.resolve_flag(STAMP, ROOT), STAMP)
            # Unverified on purpose: verifying would duplicate a catalog read
            # the first resolution performs anyway, and a bad stamp already
            # fails loudly and by name at the first fetch.
            newest.assert_not_called()

    def test_latest_resolves_to_a_concrete_stamp(self):
        with mock.patch.object(catalog, "_newest", return_value=NEWER):
            self.assertEqual(catalog.resolve_flag("latest", ROOT), NEWER)

    def test_latest_is_case_insensitive_and_stripped(self):
        for word in ("LATEST", " latest ", "Latest"):
            with self.subTest(word=word):
                with mock.patch.object(catalog, "_newest", return_value=NEWER):
                    self.assertEqual(catalog.resolve_flag(word, ROOT), NEWER)

    def test_latest_with_no_store_fails_loudly(self):
        # Asking for a pin and silently not getting one is the failure this
        # whole feature exists to remove.
        with self.assertRaises(SystemExit) as cm:
            catalog.resolve_flag("latest", "")
        self.assertIn("FDP_STORE_ROOT", str(cm.exception))

    def test_latest_with_an_empty_catalog_fails_loudly(self):
        with mock.patch.object(catalog, "_newest", return_value=""), \
             self.assertRaises(SystemExit) as cm:
            catalog.resolve_flag("latest", ROOT)
        self.assertIn(ROOT, str(cm.exception))


class TestWhatReachesTheEnvironment(unittest.TestCase):
    def test_the_word_latest_never_reaches_the_environment(self):
        # A pin meaning "whatever is newest when you read this" is not a pin.
        with mock.patch.object(catalog, "_newest", return_value=NEWER):
            env = catalog.apply_flag({"FDP_STORE_ROOT": ROOT}, "latest")
        self.assertEqual(env[catalog.VAR], NEWER)
        self.assertNotIn("latest", " ".join(env.values()).lower())

    def test_a_stamp_reaches_the_environment(self):
        env = catalog.apply_flag({"FDP_STORE_ROOT": ROOT}, STAMP)
        self.assertEqual(env[catalog.VAR], STAMP)

    def test_no_flag_leaves_the_environment_alone(self):
        # Including a value the user exported themselves.
        env = catalog.apply_flag({catalog.VAR: "catalog_USER"}, None)
        self.assertEqual(env[catalog.VAR], "catalog_USER")

    def test_the_store_root_is_taken_from_the_environment(self):
        # `fdp run` composes the device env first, so the root is in there
        # rather than passed alongside.
        with mock.patch.object(catalog, "_newest", return_value=NEWER) as n:
            catalog.apply_flag({"FDP_STORE_ROOT": ROOT}, "latest")
        n.assert_called_once_with(ROOT)


class TestDescribe(unittest.TestCase):
    def test_a_pinned_environment_says_so(self):
        got = catalog.describe({catalog.VAR: STAMP, "FDP_STORE_ROOT": ROOT})
        self.assertIn(STAMP, got)
        self.assertIn("pinned", got)

    def test_an_unpinned_environment_reports_what_would_resolve(self):
        with mock.patch.object(catalog, "_newest", return_value=NEWER):
            got = catalog.describe({"FDP_STORE_ROOT": ROOT})
        self.assertIn(NEWER, got)
        # Must not claim to be pinned: nothing is, and a run starting a
        # moment later could legitimately resolve a newer one.
        self.assertNotIn("pinned", got)

    def test_a_device_with_no_store_says_so(self):
        got = catalog.describe({})
        self.assertIn("no versioned store", got)



class TestCatalogPath(unittest.TestCase):
    def test_a_federation_url_becomes_a_namespace_path(self):
        # `fdp ls` addresses the origin by namespace path, not federation URL.
        self.assertEqual(catalog.catalog_path(ROOT), "/fdp-d3d/catalog")

    def test_a_trailing_slash_does_not_double(self):
        self.assertEqual(catalog.catalog_path(ROOT + "/"), "/fdp-d3d/catalog")

    def test_a_local_path_passes_through(self):
        self.assertEqual(catalog.catalog_path("/mnt/beegfs/data"),
                         "/mnt/beegfs/data/catalog")

    def test_a_nested_namespace_is_kept_whole(self):
        self.assertEqual(
            catalog.catalog_path("pelican://osg-htc.org:443/fdp-d3d/sub"),
            "/fdp-d3d/sub/catalog")


class TestOrderSnapshots(unittest.TestCase):
    def test_newest_first(self):
        got = catalog.order_catalogs([STAMP, NEWER, "catalog_20250101T0Z"])
        self.assertEqual(got[0], NEWER)
        self.assertEqual(got[-1], "catalog_20250101T0Z")

    def test_non_snapshots_are_dropped(self):
        # A listing is not a menu of things that can be pinned unless
        # everything in it can be.
        got = catalog.order_catalogs([STAMP, "json_indexes_20260101", "tmp"])
        self.assertEqual(got, [STAMP])

    def test_path_objects_are_accepted(self):
        from pathlib import Path
        self.assertEqual(catalog.order_catalogs([Path(STAMP)]), [STAMP])




class TestTheOldSpellingsAreRefused(unittest.TestCase):
    """B7b rule 10: refused, not ignored.

    `--snapshot` survives the rename meaning a saved-snapshot FILE, and
    `fdp catalog` takes a name that was a deprecated alias for `fdp device`.
    Both would otherwise do something quietly wrong.
    """

    def _run(self, argv):
        from fdp import cli
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            cli.main(argv)
        return cm.exception.code, err.getvalue()

    def test_run_snapshot_with_a_stamp_says_it_looks_like_a_catalog(self):
        # Asserting only that "--catalog" appears is too weak: the generic
        # "not implemented yet" message names that flag too, so the test
        # passed with the stamp check deleted. Assert the diagnosis.
        code, err = self._run(
            ["run", "--snapshot", "catalog_20260907T232802Z", "true"])
        self.assertNotEqual(code, 0)
        self.assertIn("looks like a published catalog", err)
        self.assertIn("catalog_20260907T232802Z", err)

    def test_run_snapshot_with_a_path_points_at_from_snapshot(self):
        # A saved snapshot pins a version per shot. `fdp run` composes an
        # environment for a command that chooses its own shots, so it has
        # nowhere to put that mapping -- the refusal has to hand the user
        # the call that does work, or they are simply stuck.
        code, err = self._run(["run", "--snapshot", "./mine.json", "true"])
        self.assertNotEqual(code, 0)
        self.assertIn("Pipeline.from_snapshot", err)
        self.assertIn("./mine.json", err)
        self.assertNotIn("looks like a published catalog", err)

    def test_the_refusal_does_not_promise_a_later_release(self):
        # "not implemented yet" invites a user to wait for a flag that is
        # never coming, and invites a maintainer to build the third
        # resolution path B7b spec section 5 exists to rule out.
        _, err = self._run(["run", "--snapshot", "./mine.json", "true"])
        self.assertNotIn("not implemented", err)
        self.assertNotIn("yet", err)

    def test_fdp_catalog_list_names_fdp_device(self):
        code, err = self._run(["catalog", "list"])
        self.assertNotEqual(code, 0)
        self.assertIn("fdp device list", err)


if __name__ == "__main__":
    unittest.main()
