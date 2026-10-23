from pathlib import Path
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bin/mc-tools'


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.left = self.root / 'left folder'
        self.right = self.root / 'right folder'
        self.left.mkdir(); self.right.mkdir()

    def run_tool(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.left,
                              env=env, capture_output=True, text=True)

    def test_paths_preserve_unicode_quotes_and_shell_metacharacters(self):
        name = "Türkçe ' $(touch INJECTED).txt"
        (self.left / name).write_text('data')
        r = self.run_tool('paths', '--print', '--', name)
        self.assertEqual(r.returncode, 0, r.stderr)
        import shlex
        self.assertEqual(shlex.split(r.stdout), [str(self.left / name)])
        self.assertFalse((self.left / 'INJECTED').exists())

    def test_copy_paths_uses_clipboard_input_not_a_shell(self):
        bindir = self.root / 'bin'; bindir.mkdir()
        capture = self.root / 'clipboard'
        fake = bindir / 'xsel'
        fake.write_text('#!/usr/bin/python3\nimport os,sys\nassert sys.argv[1:] == ["--clipboard", "--input"]\nopen(os.environ["CAPTURE"],"w").write(sys.stdin.read())\n')
        fake.chmod(0o755)
        name='file with spaces'; (self.left/name).touch()
        env=dict(os.environ, PATH=str(bindir), DISPLAY=':test', CAPTURE=str(capture))
        r=self.run_tool('paths','--',name,env=env)
        self.assertEqual(r.returncode,0,r.stderr)
        import shlex
        self.assertEqual(shlex.split(capture.read_text()),[str(self.left/name)])

    def test_pack_roundtrip_preserves_tree_and_symlink(self):
        (self.left / 'dizin').mkdir()
        (self.left / 'dizin' / 'ç.txt').write_bytes(b'payload')
        (self.left / 'link').symlink_to('dizin/ç.txt')
        r = self.run_tool('pack','--output','result.tar.gz','--','dizin','link')
        self.assertEqual(r.returncode,0,r.stderr)
        with tarfile.open(self.left/'result.tar.gz') as archive:
            self.assertEqual(archive.extractfile('dizin/ç.txt').read(),b'payload')
            self.assertTrue(archive.getmember('link').issym())
            self.assertEqual(archive.getmember('link').linkname,'dizin/ç.txt')
            self.assertFalse(any(x.startswith('/') or '..' in x.split('/') for x in archive.getnames()))

    def test_pack_never_overwrites_existing_output(self):
        (self.left/'result.tar.gz').write_bytes(b'keep me')
        (self.left/'file').touch()
        r=self.run_tool('pack','--output','result.tar.gz','--','file')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual((self.left/'result.tar.gz').read_bytes(),b'keep me')

    def test_pack_failure_leaves_no_partial_archive(self):
        (self.left/'file').touch()
        r=self.run_tool('pack','--output','result.tar.gz','--','file','missing')
        self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.left/'result.tar.gz').exists())
        self.assertEqual(sorted(p.name for p in self.left.iterdir()),['file'])

    def test_pack_rejects_parent_and_special_file(self):
        os.mkfifo(self.left/'pipe')
        for name in ['..','.', 'pipe']:
            with self.subTest(name=name):
                r=self.run_tool('pack','--output','result.tar.gz','--',name)
                self.assertNotEqual(r.returncode,0)
                self.assertFalse((self.left/'result.tar.gz').exists())

    def test_compare_reports_same_different_missing_and_does_not_write(self):
        for name,left,right in [('same',b'123',b'123'),('different',b'123',b'124')]:
            (self.left/name).write_bytes(left);(self.right/name).write_bytes(right)
        (self.left/'missing').touch()
        r=self.run_tool('compare','--other',str(self.right),'--','same','different','missing')
        self.assertEqual(r.returncode,1,r.stderr)
        self.assertIn('SAME  same',r.stdout);self.assertIn('DIFFERENT  different',r.stdout)
        self.assertIn('MISSING  missing',r.stdout)
        self.assertEqual((self.right/'different').read_bytes(),b'124')
        self.assertFalse((self.right/'missing').exists())

    def test_compare_rejects_symlinks_and_directories(self):
        for p in [self.left,self.right]:
            (p/'dir').mkdir();(p/'real').touch();(p/'link').symlink_to('real')
        r=self.run_tool('compare','--other',str(self.right),'--','dir','link')
        self.assertNotEqual(r.returncode,0)
        self.assertNotIn('SAME',r.stdout)


    def test_terminal_opens_in_selected_directory_with_daemon_fallback(self):
        bindir=self.root/'bin';bindir.mkdir()
        capture=self.root/'terminal-cwd'
        (bindir/'urxvtc').write_text('#!/bin/sh\nexit 1\n')
        (bindir/'urxvt').write_text('#!/usr/bin/python3\nimport os\nopen(os.environ["CAPTURE"],"w").write(os.getcwd())\n')
        for f in bindir.iterdir(): f.chmod(0o755)
        env=dict(os.environ,PATH=str(bindir),CAPTURE=str(capture))
        r=self.run_tool('terminal',str(self.right),env=env)
        self.assertEqual(r.returncode,0,r.stderr)
        import time
        deadline=time.monotonic()+2
        while not capture.exists() and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(capture.read_text(),str(self.right))

    def test_nested_special_file_cleans_temporary_archive(self):
        (self.left/'folder').mkdir();os.mkfifo(self.left/'folder'/'pipe')
        r=self.run_tool('pack','--output','result.tar.gz','--','folder')
        self.assertEqual(r.returncode,1,r.stderr)
        self.assertFalse((self.left/'result.tar.gz').exists())
        self.assertEqual(list(self.left.glob('.mc-pack-*')),[])


    def test_pack_on_filesystem_without_hardlinks(self):
        (self.left/'data.txt').write_bytes(b'USB data')
        runner = ('import errno,os,runpy,sys; '
                  'from unittest.mock import patch; '
                  'sys.argv=[sys.argv[1],"pack","--output","usb.tar.gz","--","data.txt"]; '
                  'scope=patch("os.link",side_effect=OSError(errno.EPERM,"hardlinks unsupported")); '
                  'scope.start(); runpy.run_path(sys.argv[0],run_name="__main__")')
        result=subprocess.run([sys.executable,'-c',runner,str(SCRIPT)],cwd=self.left,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        with tarfile.open(self.left/'usb.tar.gz') as archive:
            self.assertEqual(archive.extractfile('data.txt').read(),b'USB data')
        self.assertEqual(list(self.left.glob('.mc-pack-*')),[])


if __name__=='__main__': unittest.main()
