# Hira Model School Regression Tests

The regression suite exercises production service code against temporary SQLite databases only. It never opens the live database under `%LOCALAPPDATA%\HiraModelSchool`.

Covered areas:

- class-fee April baseline, later revisions, and all-section transaction rollback;
- immediate payment cancellation and automatic archive/graduation rollback integrity;
- January-to-March Full Class Promotion window enforcement;
- full-class pass/fail promotion, class-history audit entries, capacity rollback, and explicit missing-fee errors;
- explicit verification that single-record promotion and promotion reversal remain unavailable;
- Active, Dropped, reactivated Active, and prohibited manual Archived transitions;
- encrypted backup, staged restore, reverse restore, portable password recovery, and corrupt-backup rejection;
- fresh schema initialization, migration idempotence, legacy tracking-ID upgrade, duplicate-roll migration blocking, and newer-schema rejection.

Run from the project root with the project interpreter:

```bat
"E:\SchoolManagementSystem\venv\Scripts\python.exe" "E:\SchoolManagementSystem\tools\run_regression_tests.py"
```

## Unit and integration suites

Run all isolated tests with:

```text
python tools/run_all_tests.py
```

The runner discovers `tests/unit`, `tests/integration`, and `tests/regression`.
Every database test redirects `LOCALAPPDATA` to a temporary directory and must
not use the installed live school database.
