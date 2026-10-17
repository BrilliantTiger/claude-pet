"""Gating tests for "keychain quiet" — no SecurityAgent dialog from automatic paths.

User report (all Macs, v1.1.0, Developer-ID signed): ClaudePet keeps raising macOS
keychain / password dialogs. Three causes, each pinned here or in
tests/test_oauth_token_cache.py:

  (a) `oauth_token_facts()` → `_oauth_blob_noprompt()` runs
      `security find-generic-password … -w` (decrypting the secret) on every 30 s
      refresh cycle.
  (b) the 401 forced walk falls through to the native reader — the one path that
      raises "ClaudePet wants to access the keychain" — whenever the CLI is empty,
      even transiently. (Pinned in tests/test_oauth_token_cache.py, section 8.)
  (c) any `security` read while the login keychain is locked raises an unlock dialog.

Authored by the Verifier before any production change and recorded RED against
`claude/keychain-quiet` at its base (origin/dev 99f70e9). The Developer may fix
imports or docstrings here but must not touch an assertion, an expected value, or a
fixture literal (AGENTS.md §2 Condition B).

Names the implementation must provide (module level in claude_pet.py):

  * `keychain_locked()` → True | False | None. Never prompts; on darwin it reads
    `SecKeychainGetStatus` (bit `kSecUnlockStateStatus` = 1); None = unknown,
    including on non-darwin and when Security cannot be loaded.
  * `KEYCHAIN_FACTS_TTL = 1800`, `KEYCHAIN_FACTS_MIN_INTERVAL = 60`,
    `KEYCHAIN_PROMPT_WINDOW_SEC = 120`.
  * `allow_keychain_prompt()` — opens the interactive window, measured on
    `claude_pet.time.time()`.
  * `_reset_keychain_quiet_state()` — no-arg; forgets the keychain-facts cache and
    closes the prompt window. Test isolation only: without it a cache or an open
    window would leak from one test into the next and every count here would depend
    on test order. Called through `getattr` in setUp/cleanup so that its absence on
    the unfixed module fails the behavioural assertions, not setUp; its existence is
    pinned by `test_contract_names_exist`.

Expected counts below are derived from the contract text, never from running the
code. Every literal that a contract constant determines (1800, 60, 120) is written
as a literal so that a module lacking the constant fails on behaviour; the constants
themselves are pinned in `test_contract_names_exist`.

Isolation: `HOME` is a fresh temp dir (so `~/.claude/.credentials.json` resolves
inside it), `claude_pet.subprocess.run` is a recorder that never executes anything,
`claude_pet.keychain_locked` is always stubbed (`create=True`, so the unfixed module
fails on behaviour rather than on the patch), `claude_pet.time` is a settable clock
and `claude_pet.datetime.now` follows it. No test runs `security`, touches the real
Keychain, the real `~/.claude`, or `~/.claude_pet*`.
"""

import ast
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time as _real_time
import unittest
from datetime import datetime as _real_datetime, timezone
from unittest import mock

import claude_pet

SOURCE = pathlib.Path(claude_pet.__file__)

T0 = 1_800_000_000.0
TOK_A = "synthetic-access-token-A"
TOK_B = "synthetic-access-token-B"
TOK_F = "synthetic-access-token-F"
TOK_G = "synthetic-access-token-G"
NAMES = {TOK_A: "tokA", TOK_B: "tokB", TOK_F: "tokF", TOK_G: "tokG"}

# Contract literals (see module docstring).
TTL = 1800
MIN_INTERVAL = 60
MARGIN = 120          # REFRESH_MARGIN_SEC, already in the module
CYCLE = 30            # REFRESH_SEC
CYCLES = 120


class _Clock:
    def __init__(self, now):
        self.now = float(now)

    def time(self):
        return self.now

    def set(self, now):
        self.now = float(now)

    def __getattr__(self, name):
        return getattr(_real_time, name)


def blob(tok, exp_s):
    return {"accessToken": tok, "expiresAt": int(exp_s * 1000)}


def facts(tok, exp_s):
    """What a fresh read of `blob(tok, exp_s)` must yield — computed here, from the
    documented shape (expires_at tz-aware UTC, token_sig = sha256 hex[:16])."""
    return (True,
            _real_datetime.fromtimestamp(int(exp_s * 1000) / 1000.0, timezone.utc),
            hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16])


NO_FACTS = (False, None, None)


class _Security:
    """Stands in for `subprocess.run` inside claude_pet. Never executes anything.

    `value` is the claudeAiOauth dict the keychain item would hold (None = absent).
    Each `security` invocation records the clock time; anything else is recorded and
    answered with rc 1.
    """

    def __init__(self, clock):
        self.clock = clock
        self.value = None
        self.security_at = []
        self.other = []

    @property
    def calls(self):
        return len(self.security_at)

    def __call__(self, argv, *a, **k):
        argv = list(argv)
        if argv and os.path.basename(str(argv[0])) == "security":
            self.security_at.append(self.clock.time())
            if self.value is None:
                return subprocess.CompletedProcess(argv, 44, "", "not found")
            out = json.dumps({"claudeAiOauth": self.value})
            return subprocess.CompletedProcess(argv, 0, out + "\n", "")
        self.other.append(argv[:1])
        return subprocess.CompletedProcess(argv, 1, "", "")


def _reset_quiet_state():
    fn = getattr(claude_pet, "_reset_keychain_quiet_state", None)
    if fn is not None:
        fn()


class KeychainQuietCase(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="claudepet-kcq-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        for key in ("CLAUDE_PET_DEBUG", "CLAUDE_PET_USE_CLI"):
            os.environ.pop(key, None)
        self.assertEqual(os.path.expanduser("~"), self.home,
                         "HOME redirection failed; refusing to touch the real home")
        self.cred_path = os.path.join(self.home, ".claude", ".credentials.json")

        for name in ("_oauth_token_cache", "_native_state"):
            d = getattr(claude_pet, name)
            self.addCleanup(self._restore, d, dict(d))

        self.clock = _Clock(T0)
        self.sec = _Security(self.clock)
        self.locked = None           # what the keychain_locked stub answers
        clock = self.clock

        class _DT(_real_datetime):
            @classmethod
            def now(cls, tz=None):
                return _real_datetime.fromtimestamp(clock.time(), tz)

        for target, repl in (("time", self.clock), ("datetime", _DT)):
            p = mock.patch.object(claude_pet, target, repl)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(claude_pet.subprocess, "run", self.sec)
        p.start()
        self.addCleanup(p.stop)
        self.real_keychain_locked = claude_pet.__dict__.get("keychain_locked")
        p = mock.patch.object(claude_pet, "keychain_locked", lambda: self.locked, create=True)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(sys, "platform", "darwin")
        p.start()
        self.addCleanup(p.stop)

        _reset_quiet_state()
        self.addCleanup(_reset_quiet_state)

    @staticmethod
    def _restore(d, snapshot):
        d.clear()
        d.update(snapshot)

    def write_credentials(self, value):
        os.makedirs(os.path.dirname(self.cred_path), exist_ok=True)
        with open(self.cred_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"claudeAiOauth": value}))

    def facts_now(self):
        return tuple(claude_pet.oauth_token_facts())

    # ───────────── A. keychain_locked and the contract names ─────────────

    def test_contract_names_exist(self):
        """The names the implementation must provide, with the spec's values.

        | name                           | today          | **design**         |
        | ------------------------------ | -------------- | ------------------ |
        | keychain_locked                | missing ✗      | callable           |
        | allow_keychain_prompt          | missing ✗      | callable           |
        | _reset_keychain_quiet_state    | missing ✗      | callable           |
        | KEYCHAIN_FACTS_TTL             | missing ✗      | 1800               |
        | KEYCHAIN_FACTS_MIN_INTERVAL    | missing ✗      | 60                 |
        | KEYCHAIN_PROMPT_WINDOW_SEC     | missing ✗      | 120                |
        """
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        top_defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("keychain_locked", "allow_keychain_prompt", "_reset_keychain_quiet_state"):
            with self.subTest(name=name):
                self.assertTrue(name in top_defs, "module-level def %s() is missing" % name)
        for name, value in (("KEYCHAIN_FACTS_TTL", 1800),
                            ("KEYCHAIN_FACTS_MIN_INTERVAL", 60),
                            ("KEYCHAIN_PROMPT_WINDOW_SEC", 120)):
            with self.subTest(name=name):
                self.assertEqual(getattr(claude_pet, name, None), value)

    def test_keychain_locked_is_unknown_off_darwin_and_without_security(self):
        """`keychain_locked()` answers None (unknown) instead of raising or guessing,
        and never shells out, when it cannot ask Security.framework.

        | case                                | today              | always False ✗ | **design** |
        | ----------------------------------- | ------------------ | -------------- | ---------- |
        | sys.platform == "linux"             | AttributeError ✗   | False ✗        | None       |
        | darwin, Security cannot be loaded   | AttributeError ✗   | False ✗        | None       |
        | subprocess calls in either case     | —                  | —              | 0          |
        """
        fn = self.real_keychain_locked     # setUp replaced the attribute with a stub
        self.assertIsNotNone(fn, "claude_pet.keychain_locked() does not exist")
        with mock.patch.object(claude_pet.subprocess, "run", self.sec):
            with mock.patch.object(sys, "platform", "linux"):
                self.assertIsNone(fn(), "off darwin the lock state is unknown → None")
            unavailable = OSError("synthetic: Security.framework unavailable")
            with mock.patch.object(sys, "platform", "darwin"), \
                    mock.patch.object(claude_pet.ctypes, "CDLL", side_effect=unavailable), \
                    mock.patch.object(claude_pet.ctypes.cdll, "LoadLibrary", side_effect=unavailable), \
                    mock.patch.dict(sys.modules, {"Security": None}):
                self.assertIsNone(fn(), "Security unavailable → None, not a guess")
        self.assertEqual((self.sec.calls, self.sec.other), (0, []),
                         "keychain_locked() shelled out")

    # ───────────── B. no `security` subprocess while locked ─────────────

    def test_token_from_cli_runs_no_security_while_locked(self):
        """`_token_from_cli()` with the keychain item holding tokA.

        | keychain_locked() | today: result / security calls | gate only on False ✗ | **design**   |
        | ----------------- | ------------------------------ | -------------------- | ------------ |
        | True              | tokA / 1 ✗                     | None / 0             | **None / 0** |
        | False             | tokA / 1                       | tokA / 1             | **tokA / 1** |
        | None (unknown)    | tokA / 1                       | None / 0 ✗           | **tokA / 1** |

        The False and None rows rule out "never run security" and "run only when
        known unlocked" (which would break every Mac where the status read fails).
        """
        self.sec.value = blob(TOK_A, T0 + 28_800)
        for locked, want_name, want_calls in ((True, "None", 0), (False, "tokA", 1),
                                              (None, "tokA", 1)):
            with self.subTest(locked=locked):
                self.sec.security_at.clear()
                self.locked = locked
                got = claude_pet._token_from_cli()
                self.assertEqual((NAMES.get(got, str(got)), self.sec.calls),
                                 (want_name, want_calls))

    def test_oauth_blob_noprompt_while_locked_reads_file_never_security(self):
        """`_oauth_blob_noprompt()` / `oauth_token_facts()` while locked, cold cache.

        | case                        | today                         | **design**                    |
        | --------------------------- | ----------------------------- | ----------------------------- |
        | no file, keychain has tokA  | blob A / facts A / 1 call ✗   | None / (False,None,None) / 0  |
        | file has tokF               | blob F / facts F / 0 calls    | blob F / facts F / 0          |

        The file row is GREEN today on purpose: it rules out an implementation that
        returns None for everything while locked (the file never prompts).
        """
        self.locked = True
        self.sec.value = blob(TOK_A, T0 + 28_800)
        with self.subTest(case="no file"):
            self.assertIsNone(claude_pet._oauth_blob_noprompt())
            self.assertEqual(self.facts_now(), NO_FACTS)
            self.assertEqual(self.sec.calls, 0, "ran `security` while the keychain was locked")
        with self.subTest(case="file present"):
            self.sec.security_at.clear()
            self.write_credentials(blob(TOK_F, T0 + 28_800))
            self.assertEqual((claude_pet._oauth_blob_noprompt() or {}).get("accessToken"), TOK_F)
            self.assertEqual(self.facts_now(), facts(TOK_F, T0 + 28_800))
            self.assertEqual(self.sec.calls, 0)

    def test_locked_serves_cached_facts_and_never_reports_no_credentials(self):
        """Amendment to B (coordinator, Developer request): a locked keychain must
        not make recovery believe the user has no Claude credentials — that would
        flip the pill to login/onboarding while the user is merely away. Locked:
        serve the facts of the last successful read, whatever their age; with no
        such read, (False, None, None). Either way no `security` call.

        Fixture: keychain tokA expiring at T0+1000 s (so t ≥ 880 is "near expiry",
        where an unlocked keychain could be re-read early). Unlocked read at T0
        fills the cache; locked at +30 (inside the TTL), +900 (near expiry), +1830
        (past the TTL and past expiry); unlocked again at +1860; finally a cold
        cache, locked.

        | step                        | today: facts / calls | "locked → None" ✗ | "locked → cache only while fresh" ✗ | **design**      |
        | --------------------------- | -------------------- | ----------------- | ----------------------------------- | --------------- |
        | +0 unlocked                 | A / 1                | A / 1             | A / 1                               | **A / 1**       |
        | +30 locked                  | A / 2 ✗              | NO / 1 ✗          | A / 1                               | **A / 1**       |
        | +900 locked, near expiry    | A / 3 ✗              | NO / 1 ✗          | NO / 1 ✗                            | **A / 1**       |
        | +1830 locked, past TTL      | A / 4 ✗              | NO / 1 ✗          | NO / 1 ✗                            | **A / 1**       |
        | +1860 unlocked, past TTL    | A / 5                | A / 2             | A / 2                               | **A / 2**       |
        | cold cache, locked          | A / +1 ✗             | NO / +0           | NO / +0                             | **NO / +0**     |

        (NO = (False, None, None).) The +1860 row rules out "locked once → never
        read again"; the cold row rules out "always report the last facts ever seen".
        """
        exp = T0 + 1000
        self.sec.value = blob(TOK_A, exp)
        self.locked = False
        self.assertEqual(self.facts_now(), facts(TOK_A, exp))
        self.assertEqual(self.sec.calls, 1, "fixture: the first unlocked read must run security once")

        self.locked = True
        for off in (30, 900, 1830):
            with self.subTest(locked_at=off):
                self.clock.set(T0 + off)
                self.assertEqual(self.facts_now(), facts(TOK_A, exp),
                                 "locked: the facts of the last successful read must be served")
                self.assertEqual(self.sec.calls, 1, "ran `security` while the keychain was locked")

        with self.subTest(step="unlocked again past the TTL"):
            self.locked = False
            self.clock.set(T0 + 1860)
            self.assertEqual(self.facts_now(), facts(TOK_A, exp))
            self.assertEqual(self.sec.calls, 2,
                             "after unlocking past the TTL the keychain must be read exactly once")

        with self.subTest(step="cold cache, locked"):
            _reset_quiet_state()
            before = self.sec.calls
            self.locked = True
            self.assertEqual(self.facts_now(), NO_FACTS)
            self.assertEqual(self.sec.calls, before, "ran `security` while the keychain was locked")

    # ───────────── C. oauth_token_facts() must not decrypt every cycle ─────────────

    def run_cycles(self, on_cycle=None):
        """120 refresh cycles 30 s apart from T0; returns [(t_offset, facts)]."""
        out = []
        for i in range(CYCLES):
            t = T0 + CYCLE * i
            self.clock.set(t)
            if on_cycle is not None:
                on_cycle(t - T0)
            out.append((t - T0, self.facts_now()))
        return out

    def offsets(self):
        return [t - T0 for t in self.sec.security_at]

    def test_steady_state_reads_keychain_at_most_once_per_ttl(self):
        """Unlocked, no credentials file, keychain holds tokA expiring at T0+8h (never
        within the 120 s margin during the run). 120 cycles at 30 s → t = 0 … 3570 s.

        Derivation of the expected count (from the contract, not the code): cycle 0
        has no cache → 1 read. The cache is good for 1800 s, so the next read is the
        first cycle at ≥ 1800 s (or > 1800 s: 1830) — one read. The following one
        would be at ≥ 3600 s, outside the run. **2 reads**, 1800 s or more apart.

        | rival                                     | reads | gaps ≥ 1800 | values = fresh read |
        | ----------------------------------------- | ----- | ----------- | ------------------- |
        | today (decrypt every cycle)               | 120 ✗ | no ✗        | yes                 |
        | cache forever                             | 1 ✗   | —           | yes                 |
        | TTL measured in cycles / 60 s cache       | ≥ 30 ✗| no ✗        | yes                 |
        | cache, but token_sig/expiry recomputed ✗  | 2     | yes         | no ✗                |
        | **design**                                | **2** | **yes**     | **yes**             |
        """
        exp = T0 + 28_800
        self.locked = False
        self.sec.value = blob(TOK_A, exp)
        seen = self.run_cycles()
        for off, got in seen:
            if got != facts(TOK_A, exp):
                self.fail("cycle at +%ds returned facts different from a fresh read" % off)
        offs = self.offsets()
        self.assertEqual(len(offs), 2,
                         "expected 2 `security` reads in 3570 s with a 1800 s TTL, got %d" % len(offs))
        self.assertEqual(offs[0], 0, "the first cycle must read (cold cache)")
        gaps = [b - a for a, b in zip(offs, offs[1:])]
        self.assertTrue(all(g >= TTL for g in gaps), "reads closer than the TTL: %r" % offs)

    def test_near_expiry_rereads_early_but_at_most_once_per_min_interval(self):
        """Keychain holds tokA expiring at T0+1000 s. At T0+1200 s Claude Code rotates
        it: from then on the item holds tokB expiring at T0+1200+8h. Unlocked, no file,
        120 cycles at 30 s.

        Derived from the contract:
          * t = 0: cold → read.
          * t = 30 … 870: expiry is > 120 s away (130 s at 870) and the TTL has not
            run out → **no read**.
          * t ≥ 900: expiry within 120 s (then past) → re-reads allowed, each ≥ 60 s
            after the previous one.
          * tokB must be seen by t ≤ 1200 + 60 + 30 = 1290 (one min-interval plus one
            cycle of granularity after the rotation; ≥60 → 1200, >60 → 1260).
          * once tokB (expiry far) is seen, steady state resumes: the next read is
            ≥ 1800 s after the read that saw it.
          * every value is facts(A) before 1200 and facts(B) from the first B onwards.

        | rival                                  | reads before 900 | min gap | B seen by 1290 | gap after B ≥ 1800 |
        | -------------------------------------- | ---------------- | ------- | -------------- | ------------------ |
        | today (every cycle)                    | 29 ✗             | 30 ✗    | 1200           | no ✗               |
        | TTL only, no early refresh             | 0                | 1800    | no (1800) ✗    | yes                |
        | early refresh every cycle when near    | 0                | 30 ✗    | 1200           | yes                |
        | early refresh, keeps near-mode after B | 0                | 60      | ≤1260          | no ✗               |
        | **design**                             | **0**            | **≥60** | **yes**        | **yes**            |
        """
        exp_a = T0 + 1000
        rot = 1200
        exp_b = T0 + rot + 28_800
        self.locked = False
        self.sec.value = blob(TOK_A, exp_a)

        def on_cycle(off):
            if off >= rot:
                self.sec.value = blob(TOK_B, exp_b)

        seen = self.run_cycles(on_cycle)
        offs = self.offsets()
        self.assertTrue(offs and offs[0] == 0, "the first cycle must read (cold cache): %r" % offs)
        self.assertEqual([o for o in offs if 0 < o < 880], [],
                         "read the keychain before the TTL ran out or expiry came near")
        gaps = [b - a for a, b in zip(offs, offs[1:])]
        self.assertTrue(all(g >= MIN_INTERVAL for g in gaps),
                        "reads closer than the 60 s minimum interval: %r" % offs)

        fa, fb = facts(TOK_A, exp_a), facts(TOK_B, exp_b)
        for off, got in seen:
            if off < rot:
                self.assertEqual(got, fa, "cycle +%ds before the rotation" % off)
            else:
                self.assertIn(got, (fa, fb), "cycle +%ds returned neither A nor B" % off)
        first_b = next((off for off, got in seen if got == fb), None)
        self.assertIsNotNone(first_b, "the rotated token was never seen")
        self.assertLessEqual(first_b, rot + MIN_INTERVAL + CYCLE,
                             "the rotated token was seen too late for recovery")
        self.assertTrue(all(got == fb for off, got in seen if off >= first_b),
                        "fell back to the old facts after the rotation was seen")
        read_b = max(o for o in offs if o <= first_b)
        later = [o for o in offs if o > read_b]
        self.assertTrue(all(o - read_b >= TTL for o in later),
                        "after the far-expiry token was seen, re-read before the TTL: %r" % offs)

    def test_credentials_file_is_read_every_call_and_never_runs_security(self):
        """Pin of the unchanged file contract (GREEN before and after; it rules out a
        fix that also caches file facts). The file holds tokF; at cycle 10 it is
        rewritten to tokG; the keychain would answer tokA if asked.

        | rival                          | security calls | cycle 10 facts |
        | ------------------------------ | -------------- | -------------- |
        | today                          | 0              | G              |
        | cache file facts for the TTL   | 0              | F ✗            |
        | keychain before the file       | 120 ✗          | A ✗            |
        | **design**                     | **0**          | **G**          |
        """
        self.locked = False
        self.sec.value = blob(TOK_A, T0 + 28_800)
        self.write_credentials(blob(TOK_F, T0 + 28_800))

        def on_cycle(off):
            if off == 10 * CYCLE:
                self.write_credentials(blob(TOK_G, T0 + 28_800))

        seen = self.run_cycles(on_cycle)
        self.assertEqual(self.sec.calls, 0, "ran `security` although the file had the blob")
        for off, got in seen:
            want = facts(TOK_F if off < 10 * CYCLE else TOK_G, T0 + 28_800)
            self.assertEqual(got, want, "cycle +%ds" % off)


# ───────────── E. wiring: the interactive gestures open the prompt window ─────────────

class PromptWindowWiringCase(unittest.TestCase):
    """AST pins, no GUI. The methods located by reading claude_pet.py at 99f70e9:

    * **`PetView.mouseUp_`** (inside `run_gui`) — the double-click branch
      `if event.clickCount() == 2:` is the manual refresh (it zeroes `_oauth_cache`
      and calls `tk.refresh_(None)`). `allow_keychain_prompt()` must be called in
      that branch, before the `refresh_` call. It must be *in the branch*: the
      window-less tests in tests/test_companion_motion.py exec `mouseUp_` with a
      hand-built scope and single clicks, so a call outside it would be a NameError
      there.
    * **`_summary_click_work`** (inside `run_gui`), the background half of the pill
      click — `act == "login"` (`onb_login`) and `act == "recover"`
      (`token_expired`, the auth-error text). `allow_keychain_prompt()` must be
      called before the first `_recovery_step` / `start_claude_login_background`
      call. A call in its main-thread half **`_summary_click`**, before it starts the
      worker thread, is accepted instead.

    | method                      | today          | **design**                         |
    | --------------------------- | -------------- | ---------------------------------- |
    | mouseUp_ double-click branch| no call ✗      | call before `refresh_`             |
    | pill click (either half)    | no call ✗      | call before the keychain-using work|
    """

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        cls.gui = next(n for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name == "run_gui")

    @staticmethod
    def calls_in(node):
        """Calls lexically inside `node`, not descending into nested defs/lambdas."""
        out = []
        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                out.append((name, n.lineno, n))
            stack.extend(ast.iter_child_nodes(n))
        return out

    def nested_def(self, name):
        for n in self.gui.body:
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        self.fail("run_gui has no nested def %s" % name)

    def test_double_click_refresh_opens_the_prompt_window_first(self):
        cls = next(n for n in self.gui.body if isinstance(n, ast.ClassDef) and n.name == "PetView")
        mup = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "mouseUp_")
        branch = None
        for n in ast.walk(mup):
            if (isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                    and isinstance(n.test.left, ast.Call)
                    and isinstance(n.test.left.func, ast.Attribute)
                    and n.test.left.func.attr == "clickCount"
                    and len(n.test.comparators) == 1
                    and isinstance(n.test.comparators[0], ast.Constant)
                    and n.test.comparators[0].value == 2):
                branch = n
                break
        self.assertIsNotNone(branch, "mouseUp_ has no `clickCount() == 2` branch")
        calls = [c for stmt in branch.body for c in self.calls_in(ast.Module(body=[stmt], type_ignores=[]))]
        allow = [ln for name, ln, _ in calls if name == "allow_keychain_prompt"]
        refresh = [ln for name, ln, _ in calls if name == "refresh_"]
        self.assertTrue(refresh, "fixture: the double-click branch no longer calls refresh_")
        self.assertTrue(allow, "the double-click refresh does not call allow_keychain_prompt()")
        self.assertLess(min(allow), min(refresh),
                        "allow_keychain_prompt() must run before the refresh is triggered")

    def test_pill_login_and_auth_error_click_opens_the_prompt_window_first(self):
        work = self.nested_def("_summary_click_work")
        wcalls = self.calls_in(work)
        targets = [ln for name, ln, _ in wcalls
                   if name in ("_recovery_step", "start_claude_login_background")]
        self.assertTrue(targets, "fixture: _summary_click_work no longer reaches recovery/login")
        allow_w = [ln for name, ln, _ in wcalls if name == "allow_keychain_prompt"]
        if allow_w and min(allow_w) < min(targets):
            return
        click = self.nested_def("_summary_click")
        ccalls = self.calls_in(click)
        threads = [ln for name, ln, _ in ccalls if name == "Thread"]
        allow_c = [ln for name, ln, _ in ccalls if name == "allow_keychain_prompt"]
        self.assertTrue(allow_c and threads and min(allow_c) < min(threads),
                        "neither _summary_click_work (before _recovery_step / "
                        "start_claude_login_background) nor _summary_click (before the "
                        "worker thread) calls allow_keychain_prompt()")



# ───────────── R2 / R3 (Reviewer, round 2): a locked keychain reaches nothing ─────────────

class LockedKeychainReachTests(unittest.TestCase):
    """Reviewer additions R2 and R3, gated before the Developer implements them.

    R2 — **no auto-recovery spawn while locked.** The spawned `claude` CLI reads and writes
    the keychain itself, so a spawn while the login keychain is locked raises the unlock
    dialog from a child we started. Seam (named by the Verifier, at the Developer's
    offer): a pure module-level `recovery_spawn_allowed(action, locked) -> bool`, True
    only for `action == "spawn"` with `locked` not True; `_recovery_step` (closure in
    `run_gui`) calls `run_token_refresh` only under an `if` whose test calls
    `recovery_spawn_allowed(action, keychain_locked())` — or tests `keychain_locked()`
    directly — and nowhere else.

    R3 — **a locked keychain never reads as "login needed".** `claude_token_present()` is
    True when `keychain_locked()` is True and no token is cached (the token is unknown,
    not absent), so `compute_onboard_state` gives no "login"/"install" state.
    """

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        gui = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_gui")
        cls.step = next((n for n in gui.body
                         if isinstance(n, ast.FunctionDef) and n.name == "_recovery_step"), None)

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="claudepet-kcq-r3-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_PET_FORCE_ONBOARD", None)
        self.assertEqual(os.path.expanduser("~"), self.home)
        c = claude_pet._oauth_token_cache
        self.addCleanup(lambda snap=dict(c): (c.clear(), c.update(snap)))
        c["tok"] = None
        p = mock.patch.dict(claude_pet.RUNTIME, {"mode": "sub"})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(claude_pet, "_find_claude_cli", lambda: "/synthetic/bin/claude")
        p.start()
        self.addCleanup(p.stop)

    def test_recovery_spawn_allowed_truth_table(self):
        """| action           | locked | today          | always True ✗ | ignores lock ✗ | **design** |
        | ---------------- | ------ | -------------- | ------------- | -------------- | ---------- |
        | "spawn"          | False  | AttributeError | True          | True           | **True**   |
        | "spawn"          | None   | AttributeError | True          | True           | **True**   |
        | "spawn"          | True   | AttributeError | True ✗        | True ✗         | **False**  |
        | "onboard_install"| False  | AttributeError | True ✗        | False          | **False**  |
        | None             | False  | AttributeError | True ✗        | False          | **False**  |

        The None row keeps a Mac whose status read fails recovering as before."""
        fn = getattr(claude_pet, "recovery_spawn_allowed", None)
        self.assertIsNotNone(fn, "claude_pet.recovery_spawn_allowed(action, locked) does not exist")
        for action, locked, want in (("spawn", False, True), ("spawn", None, True),
                                     ("spawn", True, False), ("onboard_install", False, False),
                                     (None, False, False)):
            with self.subTest(action=action, locked=locked):
                self.assertIs(fn(action, locked), want)

    def test_recovery_step_spawns_only_when_the_keychain_is_not_locked(self):
        """AST pin of `_recovery_step` (closure in `run_gui`). Today the call is guarded by
        `if action == "spawn":` alone, so a locked keychain still spawns the CLI.

        | shape                                                     | **accepted** |
        | --------------------------------------------------------- | ------------ |
        | `if action == "spawn": run_token_refresh(state)` (today)  | no ✗         |
        | `if recovery_spawn_allowed(action, keychain_locked()):`   | yes          |
        | `if action == "spawn" and keychain_locked() is not True:` | yes          |
        | a second, unguarded `run_token_refresh` call              | no ✗         |
        """
        self.assertIsNotNone(self.step, "run_gui has no nested def _recovery_step")
        calls = [n for n in ast.walk(self.step) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "run_token_refresh"]
        self.assertTrue(calls, "fixture: _recovery_step no longer calls run_token_refresh")

        def guard_ok(test):
            text = ast.unparse(test)
            if re.search(r"\brecovery_spawn_allowed\(\s*action\s*,\s*keychain_locked\(\)\s*\)", text):
                return True
            return "keychain_locked()" in text

        parents = {}
        for n in ast.walk(self.step):
            for ch in ast.iter_child_nodes(n):
                parents[ch] = n
        for call in calls:
            node, guarded = call, False
            while node in parents:
                parent = parents[node]
                if isinstance(parent, ast.If) and node in parent.body and guard_ok(parent.test):
                    guarded = True
                    break
                node = parent
            with self.subTest(line=call.lineno):
                self.assertTrue(guarded, "run_token_refresh in _recovery_step is not guarded by "
                                         "keychain_locked() — a locked keychain still spawns the CLI")

    def test_locked_keychain_never_reads_as_login_needed(self):
        """`claude_token_present()` and the onboarding state it feeds, no cached token, no
        credentials file, the Claude CLI present, subscription mode.

        | keychain_locked() | today: present / onboard | always True ✗ | **design**      |
        | ----------------- | ------------------------ | ------------- | --------------- |
        | True              | False / "login" ✗        | True / None   | **True / None** |
        | False             | False / "login"          | True ✗ / None ✗ | **False / "login"** |
        | None (unknown)    | False / "login"          | True ✗        | **False / "login"** |

        The False and None rows keep a real logged-out user seeing the login hint.
        """
        for locked, want_present, want_state in ((True, True, None), (False, False, "login"),
                                                 (None, False, "login")):
            with self.subTest(locked=locked), \
                    mock.patch.object(claude_pet, "keychain_locked", lambda v=locked: v, create=True):
                present = claude_pet.claude_token_present()
                self.assertEqual((present, claude_pet.compute_onboard_state(None, present)),
                                 (want_present, want_state))


if __name__ == "__main__":
    unittest.main()
