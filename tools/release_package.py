"""Build deterministic extension members from a clean local commit."""

from pathlib import Path
import re
import subprocess
import zipfile


PRIVATE_MARKERS = (
    b"Users" + b"\\" + b"Melanikus",
    b"Users" + b"/" + b"Melanikus",
    b"AvP" + b"-docs",
    b"AvP" + b"-builds-excluded",
    b"sol" + b"-blender-test",
    b"BlendRIF" + b"-work",
    b"AvP" + b"-run",
    # A user folder on any drive (a drive letter, a colon, then the Users folder) names a person's computer
    b":" + b"\\" + b"Users" + b"\\",
    b":" + b"/" + b"Users" + b"/",
)
DRIVE_USER_PATH = re.compile(rb"[a-z]:[\\/]+users[\\/]", re.IGNORECASE)


def check_private_paths(entries):
    markers = tuple(marker.lower() for marker in PRIVATE_MARKERS)
    hits = []
    for name, data in entries:
        for line_number, line in enumerate(data.splitlines(), 1):
            if DRIVE_USER_PATH.search(line):
                hits.append(f"{name}:{line_number}: private drive-letter user path")
            for marker in markers:
                if marker in line.lower():
                    hits.append(f"{name}:{line_number}: private path marker {marker.decode('ascii')}")
    if hits:
        raise ValueError("Private paths in release:\n" + "\n".join(hits))


def check_archive(target):
    with zipfile.ZipFile(target) as package:
        check_private_paths((entry.filename, package.read(entry))
                            for entry in package.infolist())


def git(repo, *args):
    return subprocess.check_output(
        ["git", "--no-pager", "-C", str(repo), *args], stderr=subprocess.STDOUT)


def clean_commit(repo, ref="HEAD"):
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("Release gate requires tracked files to match HEAD")
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("ascii").strip()


def build_archive(repo, target, ref="HEAD"):
    repo, target = Path(repo), Path(target)
    commit = clean_commit(repo, ref)
    head = clean_commit(repo)
    if head != commit:
        head_names = git(repo, "ls-tree", "-r", "--name-only", head).decode("utf-8").splitlines()
        check_private_paths((name, git(repo, "show", f"{head}:{name}")) for name in head_names)
    source_names = git(repo, "ls-tree", "-r", "--name-only", commit).decode("utf-8").splitlines()
    blobs = {name: git(repo, "show", f"{commit}:{name}") for name in source_names}
    check_private_paths(blobs.items())
    required = {"blender_manifest.toml", "LICENSE", "README.md", "INSTALL.md",
                "docs/MVP.md", "docs/rif_geometry.jpg"}
    names = sorted(name for name in source_names
                   if name in required or
                   (name.endswith(".py") and ("/" not in name or name.startswith("parsers/"))))
    if not required <= set(names) or "__init__.py" not in names:
        raise ValueError("Release commit lacks required extension members")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as package:
        for name in names:
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            package.writestr(entry, blobs[name])
    check_archive(target)
    return commit
