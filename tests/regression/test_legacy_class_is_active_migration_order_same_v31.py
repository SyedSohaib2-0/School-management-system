# Regression for legacy classes tables without classes.is_active.

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LegacyClassIsActiveMigrationOrderSameV31Tests(unittest.TestCase):
    def test_legacy_classes_table_reaches_schema_23_without_data_loss(self):
        with tempfile.TemporaryDirectory(
            prefix="hms_legacy_classes_is_active_"
        ) as temp_dir:
            child_script = textwrap.dedent(
                """
                import sqlite3
                from pathlib import Path

                from app.config import DATABASE_PATH
                from app.database import (
                    CURRENT_SCHEMA_VERSION,
                    initialize_database,
                )

                database_path = Path(DATABASE_PATH)
                database_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                connection = sqlite3.connect(database_path)
                connection.execute(
                    "CREATE TABLE classes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "class_level TEXT NOT NULL, "
                    "section TEXT NOT NULL, "
                    "class_name TEXT NOT NULL UNIQUE, "
                    "student_capacity INTEGER NOT NULL DEFAULT 40, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL"
                    ")"
                )
                connection.execute(
                    "INSERT INTO classes ("
                    "class_level, section, class_name, "
                    "student_capacity, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "1",
                        "A",
                        "1A",
                        40,
                        "2026-01-01",
                        "2026-01-01",
                    ),
                )
                connection.execute("PRAGMA user_version = 14")
                connection.commit()
                connection.close()

                initialize_database()

                connection = sqlite3.connect(database_path)
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(classes)"
                    )
                }
                saved_class = connection.execute(
                    "SELECT class_name, is_active "
                    "FROM classes WHERE class_name = '1A'"
                ).fetchone()
                schema_version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                integrity = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                foreign_keys = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                index_names = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(classes)"
                    )
                }
                connection.close()

                assert "is_active" in columns, columns
                assert saved_class == ("1A", 1), saved_class
                assert schema_version == CURRENT_SCHEMA_VERSION
                assert integrity == "ok", integrity
                assert foreign_keys == [], foreign_keys
                assert (
                    "idx_classes_active_level_section"
                    in index_names
                ), index_names
                """
            )

            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = temp_dir
            environment["PYTHONPATH"] = str(PROJECT_ROOT)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["QT_QPA_PLATFORM"] = "offscreen"

            result = subprocess.run(
                [sys.executable, "-c", child_script],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )

            self.assertEqual(
                result.returncode,
                0,
                "Legacy migration subprocess failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
