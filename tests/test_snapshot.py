"""`--snapshot`: resolving the flag, and what reaches the environment.

No device package needed: these exercise the flag, not the origin. The
`needs_d3d` guard in test_env_parity.py exists because the conda build
environment has no device contributor at all, and a class that forgets it
reddens a build for a reason unrelated to what it tests.
"""

import unittest
from unittest import mock

from fdp import snapshot

STAMP = "catalog_20260907T232802Z"
NEWER = "catalog_29991231T235959Z"
ROOT = "pelican://osg-htc.org:443/fdp-d3d"


class TestResolvingTheFlag(unittest.TestCase):
    def test_a_concrete_stamp_passes_through(self):
        with mock.patch.object(snapshot, "_newest") as newest:
            self.assertEqual(snapshot.resolve_flag(STAMP, ROOT), STAMP)
            # Unverified on purpose: verifying would duplicate a catalog read
            # the first resolution performs anyway, and a bad stamp already
            # fails loudly and by name at the first fetch.
            newest.assert_not_called()

    def test_latest_resolves_to_a_concrete_stamp(self):
        with mock.patch.object(snapshot, "_newest", return_value=NEWER):
            self.assertEqual(snapshot.resolve_flag("latest", ROOT), NEWER)

    def test_latest_is_case_insensitive_and_stripped(self):
        for word in ("LATEST", " latest ", "Latest"):
            with self.subTest(word=word):
                with mock.patch.object(snapshot, "_newest", return_value=NEWER):
                    self.assertEqual(snapshot.resolve_flag(word, ROOT), NEWER)

    def test_latest_with_no_store_fails_loudly(self):
        # Asking for a pin and silently not getting one is the failure this
        # whole feature exists to remove.
        with self.assertRaises(SystemExit) as cm:
            snapshot.resolve_flag("latest", "")
        self.assertIn("FDP_STORE_ROOT", str(cm.exception))

    def test_latest_with_an_empty_catalog_fails_loudly(self):
        with mock.patch.object(snapshot, "_newest", return_value=""), \
             self.assertRaises(SystemExit) as cm:
            snapshot.resolve_flag("latest", ROOT)
        self.assertIn(ROOT, str(cm.exception))


class TestWhatReachesTheEnvironment(unittest.TestCase):
    def test_the_word_latest_never_reaches_the_environment(self):
        # A pin meaning "whatever is newest when you read this" is not a pin.
        with mock.patch.object(snapshot, "_newest", return_value=NEWER):
            env = snapshot.apply_flag({"FDP_STORE_ROOT": ROOT}, "latest")
        self.assertEqual(env[snapshot.VAR], NEWER)
        self.assertNotIn("latest", " ".join(env.values()).lower())

    def test_a_stamp_reaches_the_environment(self):
        env = snapshot.apply_flag({"FDP_STORE_ROOT": ROOT}, STAMP)
        self.assertEqual(env[snapshot.VAR], STAMP)

    def test_no_flag_leaves_the_environment_alone(self):
        # Including a value the user exported themselves.
        env = snapshot.apply_flag({snapshot.VAR: "catalog_USER"}, None)
        self.assertEqual(env[snapshot.VAR], "catalog_USER")

    def test_the_store_root_is_taken_from_the_environment(self):
        # `fdp run` composes the device env first, so the root is in there
        # rather than passed alongside.
        with mock.patch.object(snapshot, "_newest", return_value=NEWER) as n:
            snapshot.apply_flag({"FDP_STORE_ROOT": ROOT}, "latest")
        n.assert_called_once_with(ROOT)


class TestDescribe(unittest.TestCase):
    def test_a_pinned_environment_says_so(self):
        got = snapshot.describe({snapshot.VAR: STAMP, "FDP_STORE_ROOT": ROOT})
        self.assertIn(STAMP, got)
        self.assertIn("pinned", got)

    def test_an_unpinned_environment_reports_what_would_resolve(self):
        with mock.patch.object(snapshot, "_newest", return_value=NEWER):
            got = snapshot.describe({"FDP_STORE_ROOT": ROOT})
        self.assertIn(NEWER, got)
        # Must not claim to be pinned: nothing is, and a run starting a
        # moment later could legitimately resolve a newer one.
        self.assertNotIn("pinned", got)

    def test_a_device_with_no_store_says_so(self):
        got = snapshot.describe({})
        self.assertIn("no versioned store", got)



class TestCatalogPath(unittest.TestCase):
    def test_a_federation_url_becomes_a_namespace_path(self):
        # `fdp ls` addresses the origin by namespace path, not federation URL.
        self.assertEqual(snapshot.catalog_path(ROOT), "/fdp-d3d/catalog")

    def test_a_trailing_slash_does_not_double(self):
        self.assertEqual(snapshot.catalog_path(ROOT + "/"), "/fdp-d3d/catalog")

    def test_a_local_path_passes_through(self):
        self.assertEqual(snapshot.catalog_path("/mnt/beegfs/data"),
                         "/mnt/beegfs/data/catalog")

    def test_a_nested_namespace_is_kept_whole(self):
        self.assertEqual(
            snapshot.catalog_path("pelican://osg-htc.org:443/fdp-d3d/sub"),
            "/fdp-d3d/sub/catalog")


class TestOrderSnapshots(unittest.TestCase):
    def test_newest_first(self):
        got = snapshot.order_snapshots([STAMP, NEWER, "catalog_20250101T0Z"])
        self.assertEqual(got[0], NEWER)
        self.assertEqual(got[-1], "catalog_20250101T0Z")

    def test_non_snapshots_are_dropped(self):
        # A listing is not a menu of things that can be pinned unless
        # everything in it can be.
        got = snapshot.order_snapshots([STAMP, "json_indexes_20260101", "tmp"])
        self.assertEqual(got, [STAMP])

    def test_path_objects_are_accepted(self):
        from pathlib import Path
        self.assertEqual(snapshot.order_snapshots([Path(STAMP)]), [STAMP])

if __name__ == "__main__":
    unittest.main()
