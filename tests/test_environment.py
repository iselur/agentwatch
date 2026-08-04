"""What agentwatch does when the environment it was handed is odd.

A watcher is the thing somebody leaves running in a pane, and the panes it gets
left running in are often not a developer laptop: a container started with
``--user 1001``, a Kubernetes pod with ``runAsUser`` set, a systemd unit, a cron
entry.  All of those hand a process a ``HOME`` that names a directory nobody
created.

There is a real difference between a path the user typed and a path the
environment happened to be carrying.  Typing ``--home /wrong`` is a mistake and
deserves to be said out loud.  Inheriting a ``HOME`` that does not exist is not
a mistake at all — it just means there are no sessions there yet, which is a
case this tool already knows how to say.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(**overrides):
    env = dict(os.environ)
    env["PYTHONPATH"] = _ROOT
    env.pop("AGENTWATCH_HOME", None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, "-m", "agentwatch", "--once", "--since", "10m"]
        + list(args),
        capture_output=True, text=True, encoding="utf-8", cwd=_ROOT,
        timeout=60, **kwargs)


class TestAHomeThatIsNotThere(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentwatch_env_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_inherited_home_that_does_not_exist_is_an_empty_day(self):
        # `docker run --user 1001` with no home created for that uid.  The
        # answer is "nothing has happened yet", not a usage screen for a flag
        # the person never typed.
        result = _run(env=_env(HOME=os.path.join(self.tmp, "nobody")))
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        self.assertNotIn("usage:", result.stderr, result.stderr)

    def test_an_inherited_home_that_is_a_file_is_an_empty_day(self):
        # The same shape, reached a different way: something already occupies
        # the name, so `isdir` is false for a reason that is not the user's.
        path = os.path.join(self.tmp, "afile")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("")
        result = _run(env=_env(HOME=path))
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)

    def test_a_home_the_user_typed_is_still_an_error(self):
        # The other half of the same rule.  A typo here is worth saying out
        # loud, because the person meant a particular directory and did not
        # get it — silence would look like a quiet afternoon on the wrong box.
        missing = os.path.join(self.tmp, "typo")
        result = _run("--home", missing, env=_env())
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(missing, result.stderr, result.stderr)

    def test_a_home_named_in_the_environment_is_still_an_error(self):
        # `AGENTWATCH_HOME` is nobody's accident — it is set on purpose, so
        # getting it wrong is the same mistake as typing it wrong.
        missing = os.path.join(self.tmp, "onpurpose")
        result = _run(env=_env(AGENTWATCH_HOME=missing))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(missing, result.stderr, result.stderr)

    def test_no_home_variable_at_all_does_not_crash(self):
        # A systemd unit with no `User=` and no environment file.  Python
        # falls back to the passwd database here, so this should behave like
        # an ordinary run rather than raising on the way in.
        result = _run(env=_env(HOME=None))
        self.assertNotIn("Traceback", result.stderr, result.stderr)


if __name__ == "__main__":
    unittest.main()
