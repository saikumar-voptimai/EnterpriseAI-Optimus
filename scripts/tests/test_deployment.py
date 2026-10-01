"""Deployment invariants, independent of Docker and production configuration."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]


def load_config(root):
    spec = importlib.util.spec_from_file_location("isolated_configure", SCRIPTS / "configure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    module.ENV = root / ".env"
    return module


class ConfigurationTests(unittest.TestCase):
    def test_create_only_keeps_existing_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env.example").write_text("POSTGRES_PASSWORD=\nCREDENTIAL_ENCRYPTION_KEY=\n")
            module = load_config(root)
            module.save_values(
                {"POSTGRES_PASSWORD": "first", "CREDENTIAL_ENCRYPTION_KEY": "key"}, create_only=True
            )
            with self.assertRaises(FileExistsError):
                module.save_values({"POSTGRES_PASSWORD": "replacement"}, create_only=True)
            self.assertEqual(module.read_values()["POSTGRES_PASSWORD"], "first")
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)

    def test_values_are_literal_and_not_shell_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env.example").write_text("TOKEN=\n")
            module = load_config(root)
            module.save_values({"TOKEN": "literal-$HOME-$(id)"}, create_only=True)
            self.assertEqual(module.read_values()["TOKEN"], "literal-$HOME-$(id)")
            self.assertIn("TOKEN='literal-$HOME-$(id)'", (root / ".env").read_text())

    def test_symlink_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "other").write_text("unchanged")
            (root / ".env").symlink_to(root / "other")
            with self.assertRaises(SystemExit):
                load_config(root).save_values({"TOKEN": "new"})
            self.assertEqual((root / "other").read_text(), "unchanged")


class ArchiveTests(unittest.TestCase):
    def command(self, operation, directory, archive=None, stdin=None):
        args = [
            sys.executable,
            str(SCRIPTS / "volume_archive.py"),
            operation,
            "--data-dir",
            str(directory),
        ]
        if archive:
            args += ["--archive", str(archive)]
        return subprocess.run(args, input=stdin, capture_output=True)

    def test_roundtrip_and_restore_refuses_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "documents").mkdir(parents=True)
            (source / "documents" / "original.bin").write_bytes(bytes(range(256)))
            packed = self.command("pack", source)
            self.assertEqual(packed.returncode, 0, packed.stderr)
            dest = root / "destination"
            (dest / "documents").mkdir(parents=True)
            restored = self.command("unpack", dest, stdin=packed.stdout)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual((dest / "documents" / "original.bin").read_bytes(), bytes(range(256)))
            self.assertNotEqual(self.command("unpack", dest, stdin=packed.stdout).returncode, 0)

    def test_traversal_and_symlink_rejected(self):
        for name, type_ in [("../outside.txt", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    entry = tarfile.TarInfo(name)
                    entry.type = type_
                    entry.linkname = "/etc/passwd" if type_ == tarfile.SYMTYPE else ""
                    output.addfile(entry, io.BytesIO(b""))
                result = self.command("validate", root / "data", archive)
                self.assertNotEqual(result.returncode, 0)
                result = self.command("unpack", root / "data", archive)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "outside.txt").exists())
                self.assertEqual(list((root / "data").iterdir()), [])

    def test_duplicate_entry_fails_without_partial_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "duplicate.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                for payload in [b"first", b"second"]:
                    entry = tarfile.TarInfo("note.txt")
                    entry.size = len(payload)
                    output.addfile(entry, io.BytesIO(payload))
            result = self.command("unpack", root / "data", archive)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list((root / "data").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
