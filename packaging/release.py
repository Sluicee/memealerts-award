"""Refuse incomplete release sets and write SHA-256 checksums."""
import hashlib
import os
from pathlib import Path

TARGETS = {
    "linux-x64": "tar.gz", "linux-arm64": "tar.gz",
    "windows-x64": "zip", "windows-arm64": "zip",
    "macos-x64": "tar.gz", "macos-arm64": "tar.gz",
}


def write_checksums(directory, tag):
    expected = {f"MemeAlertsAward-{tag}-{target}.{extension}" for target, extension in TARGETS.items()}
    archives = sorted(path for path in directory.iterdir() if path.name != "SHA256SUMS.txt")
    if {path.name for path in archives} != expected or any(not path.is_file() for path in archives):
        raise ValueError("Release archive set is incomplete or unexpected")
    lines = []
    for path in archives:
        with path.open("rb") as archive:
            digest = hashlib.file_digest(archive, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    (directory / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    write_checksums(Path("release"), os.environ["RELEASE_TAG"])
