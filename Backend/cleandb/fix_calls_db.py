"""
Wrapper script to run the existing clean_db.clean_database() logic from inside cleandb/.
This keeps all DB-fix utilities discoverable under Backend/cleandb as planned.
"""

from pathlib import Path
import sys


def main():
    backend_dir = Path(__file__).parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    import clean_db  # type: ignore

    clean_db.clean_database()


if __name__ == "__main__":
    main()

