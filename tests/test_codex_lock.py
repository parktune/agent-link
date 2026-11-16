import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from agent_link.codex_app import CodexAppError, check_thread_available

@unittest.skipUnless(os.name == 'posix', 'requires POSIX flock')
class CodexWriterLockTests(unittest.TestCase):
    def test_busy_thread_is_not_launched_or_unlocked(self):
        import fcntl
        from agent_link import cli
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, CODEX_HOME=root):
            path = Path(root, 'thread-writer-locks', 'thread-1.lock')
            path.parent.mkdir()
            with path.open('wb') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                inode = path.stat().st_ino
                with mock.patch('agent_link.sessions.find_codex_sessions', return_value=[{'id': 'thread-1', 'cwd': root}]), mock.patch.object(cli.shutil, 'which', return_value='codex'), mock.patch.object(cli.os, 'chdir'), mock.patch.object(cli, 'launch') as launch:
                    with self.assertRaisesRegex(SystemExit, 'open in another app'):
                        cli.open_codex('resume', 'trainer')
                    launch.assert_not_called()
                self.assertEqual(path.stat().st_ino, inode)
                with self.assertRaisesRegex(CodexAppError, 'Do not delete'):
                    check_thread_available('thread-1')
            check_thread_available('thread-1')

    def test_stale_file_is_not_removed_and_probe_releases_its_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, CODEX_HOME=root):
            path = Path(root, 'thread-writer-locks', 'thread-1.lock')
            path.parent.mkdir()
            path.touch()
            check_thread_available('thread-1')
            self.assertTrue(path.exists())
            with path.open('rb') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_thread_available('missing')
            self.assertFalse(path.with_name('missing.lock').exists())
