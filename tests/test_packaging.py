import hashlib
import os
import runpy
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
build = runpy.run_path(str(ROOT / "packaging" / "build.py"))
release = runpy.run_path(str(ROOT / "packaging" / "release.py"))


class PackagingTests(unittest.TestCase):
    def test_runner_platform_names(self):
        for system, machine, target in (("Linux", "aarch64", "linux-arm64"),
                                         ("Windows", "AMD64", "windows-x64"),
                                         ("Windows", "ARM64", "windows-arm64"),
                                         ("Darwin", "arm64", "macos-arm64")):
            self.assertEqual(build["native_target"](system, machine), target)
        with self.assertRaises(ValueError):
            build["native_target"]("Linux", "riscv64")

    def test_unsafe_build_versions_are_rejected(self):
        for version in ("../release", "v1/other", "", "$(id)", "v1\nother"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                build["validate_version"](version)
        self.assertEqual(build["validate_version"]("v0.1.0-beta.1"), "v0.1.0-beta.1")

    def test_windows_archive_keeps_bundle_contents_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "MemeAlertsAward"
            (bundle / "_internal").mkdir(parents=True)
            (bundle / "MemeAlertsAward.exe").write_bytes(b"binary")
            (bundle / "_internal" / "library.dll").write_bytes(b"library")
            path = build["archive_bundle"](bundle, root / "release", "v0.1.0", "windows-x64")
            with zipfile.ZipFile(path) as archive:
                self.assertIn("MemeAlertsAward/MemeAlertsAward.exe", archive.namelist())
                self.assertIn("MemeAlertsAward/_internal/library.dll", archive.namelist())

    @unittest.skipIf(os.name == "nt", "Executable modes and native symlinks are POSIX features")
    def test_tar_preserves_executable_mode_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "MemeAlertsAward"
            bundle.mkdir()
            executable = bundle / "MemeAlertsAward"
            executable.write_bytes(b"binary")
            executable.chmod(0o755)
            (bundle / "shortcut").symlink_to(executable.name)
            path = build["archive_bundle"](bundle, root / "release", "v0.1.0", "linux-x64")
            with tarfile.open(path) as archive:
                self.assertEqual(archive.getmember("MemeAlertsAward/MemeAlertsAward").mode & 0o111, 0o111)
                self.assertTrue(archive.getmember("MemeAlertsAward/shortcut").issym())

    def test_release_requires_complete_set_and_hashes_all_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for target, extension in release["TARGETS"].items():
                (directory / f"MemeAlertsAward-v0.1.0-{target}.{extension}").write_bytes(target.encode())
            release["write_checksums"](directory, "v0.1.0")
            lines = (directory / "SHA256SUMS.txt").read_text().splitlines()
            self.assertEqual(len(lines), 6)
            for line in lines:
                digest, name = line.split("  ")
                self.assertEqual(digest, hashlib.sha256((directory / name).read_bytes()).hexdigest())
            # Retrying checksum generation must be deterministic.
            release["write_checksums"](directory, "v0.1.0")
            self.assertEqual((directory / "SHA256SUMS.txt").read_text().splitlines(), lines)
            (directory / lines[0].split("  ")[1]).unlink()
            with self.assertRaises(ValueError):
                release["write_checksums"](directory, "v0.1.0")

    def test_wrong_tag_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for target, extension in release["TARGETS"].items():
                (directory / f"MemeAlertsAward-v0.1.0-{target}.{extension}").write_bytes(b"archive")
            with self.assertRaises(ValueError):
                release["write_checksums"](directory, "v0.2.0")
            self.assertFalse((directory / "SHA256SUMS.txt").exists())
