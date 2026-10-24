"""Build, smoke-check and archive one native desktop release."""
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

APP = "MemeAlertsAward"


def native_target(system, machine):
    architectures = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    systems = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}
    try:
        return systems[system] + "-" + architectures[machine.lower()]
    except KeyError:
        raise ValueError(f"Unsupported build platform: {system} {machine}") from None


def validate_version(version):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Build version must be safe for archive filenames")
    return version


def write_icons(directory):
    # Reuse the application's drawn icon rather than maintaining a second visual design.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from PIL import Image
    from memeaward.ui import icon

    app = QApplication.instance() or QApplication([])
    directory.mkdir(parents=True, exist_ok=True)
    png = directory / "icon.png"
    if not icon().pixmap(256, 256).save(str(png)):
        raise RuntimeError("Cannot render application icon")
    with Image.open(png) as source:
        source.save(directory / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        source.resize((1024, 1024)).save(directory / "icon.icns")
    app.processEvents()


def archive_bundle(bundle, output, version, target):
    output.mkdir(parents=True, exist_ok=True)
    name = output / f"{APP}-{validate_version(version)}-{target}"
    if target.startswith("windows-"):
        return Path(shutil.make_archive(str(name), "zip", root_dir=bundle.parent, base_dir=bundle.name))
    archive_path = Path(str(name) + ".tar.gz")
    # Preserve executable permissions and macOS framework symlinks.
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(bundle, arcname=bundle.name)
    return archive_path


def main():
    system = platform.system()
    target = native_target(system, platform.machine())
    if os.environ.get("BUILD_PLATFORM", target) != target:
        raise SystemExit(f"Runner architecture does not match BUILD_PLATFORM: {target}")
    version = validate_version(os.environ.get("BUILD_VERSION", "local"))
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(root / "build" / "pyinstaller-cache"))
    icons = root / "build" / "icons"
    write_icons(icons)
    backend = {"Linux": "SecretService", "Windows": "Windows", "Darwin": "macOS"}[system]
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
               "--name", APP, "--hidden-import", f"keyring.backends.{backend}"]
    if system == "Windows":
        command += ["--icon", str(icons / "icon.ico")]
    elif system == "Darwin":
        command += ["--icon", str(icons / "icon.icns"), "--osx-bundle-identifier", "com.sluicee.memealertsaward"]
    subprocess.run(command + ["app.py"], check=True)
    bundle = root / "dist" / (APP + ".app" if system == "Darwin" else APP)
    executable = bundle / {"Linux": APP, "Windows": APP + ".exe", "Darwin": "Contents/MacOS/" + APP}[system]
    if not executable.is_file():
        raise SystemExit(f"Build executable missing: {executable}")
    subprocess.run([str(executable), "--self-test"], check=True, timeout=60,
                   env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    readme_directory = bundle / "Contents/Resources" if system == "Darwin" else bundle
    readme_directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "packaging" / "README.txt", readme_directory / "README.txt")
    archive = archive_bundle(bundle, root / "release", version, target)
    print(f"Release archive: {archive}")


if __name__ == "__main__":
    main()
