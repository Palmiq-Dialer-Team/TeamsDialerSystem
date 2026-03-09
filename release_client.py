"""
Release script for TeamsPowerDialer client.
Run when ready to publish an update. It will:
1. Bump semantic version (x.y.z) in Installer/local_version.json and WebUpdates/newversion.json
   (version.json is left unchanged for backwards compatibility with older launchers.)
2. Zip Installer contents (excluding __pycache__, .pyc, user_settings.json)
3. Move current WebUpdates/client_package.zip to WebUpdates/backup/
4. Place the new zip as WebUpdates/client_package.zip
"""
import json
import shutil
import zipfile
from pathlib import Path
import subprocess
from datetime import datetime

# Paths (script assumes it runs from project root)
PROJECT_ROOT = Path(__file__).resolve().parent
INSTALLER_DIR = PROJECT_ROOT / "Installer"
WEBUPDATES_DIR = PROJECT_ROOT / "WebUpdates"
BACKUP_DIR = WEBUPDATES_DIR / "backup"


# Files/folders to exclude from the package
EXCLUDE = {
    "__pycache__",
    ".pyc",
    "user_settings.json",  # User-specific, protected on update
}


def _parse_version_tuple(raw) -> tuple[int, int, int]:
    """
    Convert raw version (float/int/str) to a (major, minor, patch) tuple.
    Supports:
      - legacy float style: 1.92 => (1, 9, 2)
      - '1.9.2' string style => (1, 9, 2)
      - '1.92' string style  => (1, 9, 2)
    """
    try:
        s = str(raw).strip()
        if not s:
            return (0, 0, 0)
        parts = s.split(".")
        # New style: x.y.z
        if len(parts) == 3:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        # Fallback: x.yy -> map yy -> minor, patch (e.g. 1.92 -> 1.9.2)
        if len(parts) == 2:
            major = int(parts[0])
            frac_digits = "".join(ch for ch in parts[1] if ch.isdigit())
            if not frac_digits:
                return (major, 0, 0)
            if len(frac_digits) == 1:
                minor = int(frac_digits[0])
                patch = 0
            else:
                minor = int(frac_digits[0])
                patch = int(frac_digits[1:])
            return (major, minor, patch)
    except Exception:
        pass
    return (0, 0, 0)


def _version_tuple_to_str(v: tuple[int, int, int]) -> str:
    """Format (major, minor, patch) as x.y.z string."""
    major, minor, patch = v
    return f"{major}.{minor}.{patch}"


def get_current_version() -> str:
    """Read current version as a string (semantic x.y.z), from local or newversion.json."""
    for p in (INSTALLER_DIR / "local_version.json", WEBUPDATES_DIR / "newversion.json"):
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("version", "0.0.0")
                # Normalize to x.y.z string
                v_tuple = _parse_version_tuple(raw)
                return _version_tuple_to_str(v_tuple)
            except (json.JSONDecodeError, OSError, ValueError):
                pass
    return "0.0.0"


def bump_version(current: str) -> str:
    major, minor, patch = _parse_version_tuple(current)
    assert major>=0
    assert 0 <=minor<=99
    assert 0 <=patch<=99
    patch +=1
    if patch >9:
        patch =0
        minor+=1
        if minor>9:
            minor=0
            major+=1

    return _version_tuple_to_str((major, minor, patch))


def write_version(version: str):
    """Update local_version.json and newversion.json only (version.json stays for old launchers)."""
    for p in (INSTALLER_DIR / "local_version.json", WEBUPDATES_DIR / "newversion.json"):
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": version}, f, indent=4)
        print(f"  Updated {p.relative_to(PROJECT_ROOT)} -> {version}")


def should_exclude(name: str, path: Path) -> bool:
    if "user_settings.json" in name:
        return True
    if "__pycache__" in path.parts:
        return True
    if name.endswith(".pyc"):
        return True
    return False


def create_package(version: str) -> Path:
    """Zip Installer contents, return path to the new zip."""
    temp_zip = PROJECT_ROOT / f"client_package_{version}.zip"
    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INSTALLER_DIR.rglob("*"):
            if item.is_file() and not should_exclude(item.name, item):
                arcname = item.relative_to(INSTALLER_DIR)
                z.write(item, arcname)
                print(f"  + {arcname}")
    return temp_zip



def main():
    print("TeamsPowerDialer - Client Release")
    print("-" * 40)

    current = get_current_version()
    new_ver = bump_version(current)
    print(f"Current version: {current} -> New version: {new_ver}\n")

    # 1. Update version files
    print("1. Updating version files...")
    write_version(new_ver)

    # 2. Create zip
    print("\n2. Creating client package...")
    temp_zip = create_package(new_ver)

    # 3. Backup old package, place new one
    print("\n3. Replacing client_package.zip...")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    current_package = WEBUPDATES_DIR / "client_package.zip"
    backup_dest = BACKUP_DIR / "client_package.zip"

    if current_package.exists():
        shutil.move(str(current_package), str(backup_dest))
        print(f"  Moved previous package to {backup_dest.relative_to(PROJECT_ROOT)}")
    else:
        print("  No previous client_package.zip found (first release).")

    shutil.move(str(temp_zip), str(current_package))
    print(f"  New package placed at {current_package.relative_to(PROJECT_ROOT)}")

    print("\nDone. Clients will pick up version", new_ver, "on next launch.")


if __name__ == "__main__":
    main()
