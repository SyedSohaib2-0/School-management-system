# Fixes 3–8 Implementation Report

## Baseline

Source package: `HiraModelSchool_CompleteSource_20260728_042332(1).zip`

The original uploaded archive was not modified. All changes were made in a separate working copy.

## Fix 3 — Unique recovery salt per encrypted backup

- Encrypted backup container format increased from version 1 to version 2.
- Every new `.hmsbackup` now receives a new random Scrypt recovery salt.
- The local Backup Recovery Password is stored only as Windows-DPAPI-protected ciphertext so automatic backups can derive a fresh recovery key for every backup.
- The readable password is never written to disk.
- Existing version-1 backup containers remain readable.
- Existing version-1 key stores are detected as `Upgrade Required` and can be upgraded after the administrator re-enters the existing recovery password.
- The master key and key ID are retained during upgrade, so existing backups remain restorable.

## Fix 4 — Restore completion atomicity

- Restore history is now persisted before the live database is replaced.
- A restore-history write failure cancels the restore while the live database is still unchanged.
- If the atomic live database replacement fails, the previous restore state is restored.
- No post-replacement history write can cause the software to report a failed restore after the live database has already changed.

## Fix 5 — Obsolete USB-specific implementation removed

Removed from production code:

- USB backup prefix and USB service methods.
- USB automatic-backup branch.
- USB settings keys.
- USB tab, controls, tables, actions, refresh logic and restore-source routing.

A removable drive can still be selected through the supported **Selected Folder** destination.

## Fix 6 — Obsolete class command-line tool removed

Removed:

- `app/manage_classes.py`

The supported Class Management user interface and service remain unchanged.

## Fix 7 — Obsolete test executable specification removed

Removed:

- `HiraModelSchoolTest.spec`

The production `HiraModelSchool.spec` remains.

## Fix 8 — Unit and integration testing added

Added test tiers:

- `tests/unit/`
- `tests/integration/`
- existing `tests/regression/`
- `tools/run_all_tests.py`

Coverage added for:

- per-backup unique Scrypt salts;
- key-store plaintext-secret prevention;
- version-1 key-store upgrade;
- tampered encrypted-header rejection;
- same-computer encrypted restore;
- portable recovery-password restore;
- automatic encrypted backup without USB destination;
- restore-history failure before live replacement;
- database-replacement failure and restore-state rollback;
- legacy USB settings removal;
- obsolete repository-file removal.

## Final test result in the inspection environment

- Total discovered: 26
- Passed: 25
- Failed: 0
- Errors: 0
- Skipped: 1

The skipped test is the live PySide6 Backup Management widget-construction test because PySide6 is not installed in the inspection interpreter. The source-level repository test confirms that the USB implementation is absent. The UI test remains in the suite and will execute automatically in the project Windows virtual environment where PySide6 is installed.

## Release status

These fixes do not rebuild the executable or installer. Missing build assets identified separately must be restored before the production build stage.
