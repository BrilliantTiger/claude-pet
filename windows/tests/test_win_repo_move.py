"""Gates for the repository move uygnoey/claude-pet -> BrilliantTiger/claude-pet (Windows half).

Run from the worktree root:

    python3 -m unittest discover -s windows/tests -t . -v

Verifier: repo-verify (AGENTS.md §2 Condition A — the Developer must not touch the
assertions or fixtures here). The macOS half, with the full truth table, is
``tests/test_repo_move.py``. Expected addresses are literals, never rebuilt from
``cp.GITHUB_REPO`` (that would be green for any value of the constant).

| fixture                                | unchanged (old) | correct        | owner pinned to GITHUB_REPO | owner pinned to old |
| -------------------------------------- | --------------- | -------------- | --------------------------- | ------------------- |
| win_update.LATEST_RELEASE_URL          | uygnoey  RED    | BrilliantTiger |  —                          |  —                  |
| URL check_github_update_win fetches    | uygnoey  RED    | BrilliantTiger |  —                          |  —                  |
| installer.iss AppSupportURL            | uygnoey  RED    | BrilliantTiger |  —                          |  —                  |
| installer.iss AppUpdatesURL            | uygnoey  RED    | BrilliantTiger |  —                          |  —                  |
| asset URL under .../uygnoey/...        | accepted        | accepted       | REJECTED                    | accepted            |
| asset URL under .../BrilliantTiger/... | accepted        | accepted       | accepted                    | REJECTED            |

The two asset rows are regression guards, green on the unchanged code by design:
``_asset_url_ok`` checks scheme and host only. Installed copies must keep accepting
whichever owner GitHub writes into ``browser_download_url``.
"""

import importlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ISS = os.path.join(ROOT, "windows", "installer.iss")

NEW_API_LATEST = "https://api.github.com/repos/BrilliantTiger/claude-pet/releases/latest"
NEW_SUPPORT = "https://github.com/BrilliantTiger/claude-pet"
NEW_UPDATES = "https://github.com/BrilliantTiger/claude-pet/releases/latest"
OLD_DL = "https://github.com/uygnoey/claude-pet/releases/download/v9.9/"
NEW_DL = "https://github.com/BrilliantTiger/claude-pet/releases/download/v9.9/"


def _wu():
    return importlib.import_module("windows.win_update")


def _iss_setup_value(key):
    """The value of ``key=`` in the ``[Setup]`` section; exactly one occurrence."""
    with open(ISS, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    section, found = None, []
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
            continue
        if section == "[setup]" and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            if k.strip().lower() == key.lower():
                found.append(v.strip())
    if len(found) != 1:
        raise AssertionError("%s: expected one [Setup] entry, found %r" % (key, found))
    return found[0]


class WinUpdateUrlTests(unittest.TestCase):
    def test_latest_release_url_is_the_new_repository(self):
        self.assertEqual(_wu().LATEST_RELEASE_URL, NEW_API_LATEST)

    def test_check_fetches_the_new_repository(self):
        calls = []

        def fetch_json(url):
            calls.append(url)
            return {"tag_name": "v0.0.1", "assets": []}

        result = _wu().check_github_update_win(fetch_json, "AMD64", "inno", "1.1.0")
        self.assertEqual(calls, [NEW_API_LATEST])
        self.assertEqual(result, ("current", "0.0.1"))


class InstallerUrlTests(unittest.TestCase):
    def test_support_url(self):
        self.assertEqual(_iss_setup_value("AppSupportURL"), NEW_SUPPORT)

    def test_updates_url(self):
        self.assertEqual(_iss_setup_value("AppUpdatesURL"), NEW_UPDATES)


class WinAssetOwnerAgnosticTests(unittest.TestCase):
    """Regression guard: green before and after the move."""

    def test_asset_url_ok_accepts_both_owners(self):
        wu = _wu()
        for base in (OLD_DL, NEW_DL):
            for name in (wu.SETUP_ASSET, wu.ZIP_ASSET):
                with self.subTest(url=base + name):
                    self.assertIs(wu._asset_url_ok(base + name), True)

    def test_selection_accepts_both_owners(self):
        wu = _wu()
        for base in (OLD_DL, NEW_DL):
            for kind, name in (("inno", wu.SETUP_ASSET), ("portable", wu.ZIP_ASSET)):
                with self.subTest(base=base, kind=kind):
                    assets = [{"name": name, "browser_download_url": base + name,
                               "size": 1, "digest": "sha256:" + "ab" * 32}]
                    got = wu.select_update_asset_win(assets, "AMD64", kind)
                    self.assertIsInstance(got, dict)
                    self.assertEqual(got["url"], base + name)


if __name__ == "__main__":
    unittest.main()
