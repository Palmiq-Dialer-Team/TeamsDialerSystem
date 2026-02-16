"""
Release script for Test Client.
Run when ready to publish a tester update. It will:
1. Bump version in TestClient/local_version.json and WebUpdates/tester_version.json
2. Zip TestClient contents (excluding __pycache__, .pyc)
3. Move current WebUpdates/tester_package.zip to WebUpdates/backup/
4. Place the new zip as WebUpdates/tester_package.zip
"""
import json
import shutil
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TESTCLIENT_DIR = PROJECT_ROOT / "TestClient"
WEBUPDATES_DIR = PROJECT_ROOT / "WebUpdates"
BACKUP_DIR = WEBUPDATES_DIR / "backup"


def should_exclude(name: str, path: Path) -> bool:
    if "__pycache__" in path.parts:
        return True
    if name.endswith(".pyc"):
        return True
    return False


def get_current_version():
    for p in (TESTCLIENT_DIR / "local_version.json", WEBUPDATES_DIR / "tester_version.json"):
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return float(json.load(f).get("version", 0))
            except (json.JSONDecodeError, ValueError):
                pass
    return 0.0


def bump_version(current: float) -> float:
    return round(current + 0.01, 2)


def write_version(version: float):
    for p in (TESTCLIENT_DIR / "local_version.json", WEBUPDATES_DIR / "tester_version.json"):
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": version}, f, indent=4)
        print(f"  Updated {p.relative_to(PROJECT_ROOT)} -> {version}")


def create_package(version: float) -> Path:
    temp_zip = PROJECT_ROOT / f"tester_package_{version}.zip"
    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for item in TESTCLIENT_DIR.rglob("*"):
            if item.is_file() and not should_exclude(item.name, item):
                arcname = item.relative_to(TESTCLIENT_DIR)
                z.write(item, arcname)
                print(f"  + {arcname}")
    return temp_zip


def main():
    print("Test Client - Tester Release")
    print("-" * 40)

    current = get_current_version()
    new_ver = bump_version(current)
    print(f"Current version: {current} -> New version: {new_ver}\n")

    print("1. Updating version files...")
    write_version(new_ver)

    print("\n2. Creating tester package...")
    temp_zip = create_package(new_ver)

    print("\n3. Replacing tester_package.zip...")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    current_package = WEBUPDATES_DIR / "tester_package.zip"
    backup_dest = BACKUP_DIR / "tester_package.zip"

    if current_package.exists():
        shutil.move(str(current_package), str(backup_dest))
        print(f"  Moved previous package to {backup_dest.relative_to(PROJECT_ROOT)}")
    else:
        print("  No previous tester_package.zip found (first release).")

    shutil.move(str(temp_zip), str(current_package))
    print(f"  New package placed at {current_package.relative_to(PROJECT_ROOT)}")

    print("\nDone. Test clients will pick up version", new_ver, "on next launch.")


if __name__ == "__main__":
    main()
