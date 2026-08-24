# Hira Model School Management System

<p align="center">
  <img src="assets/images/hira_model_school_logo.png" alt="Hira Model School Logo" width="150">
</p>

<p align="center">
  <strong>Offline Windows desktop school-management system for student records, classes, fees, promotions, finance, reporting, backups, and controlled software activation.</strong>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-blue">
  <img alt="PySide6" src="https://img.shields.io/badge/GUI-PySide6-41CD52">
  <img alt="SQLite" src="https://img.shields.io/badge/Database-SQLite-003B57">
  <img alt="Windows" src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4">
  <img alt="Version" src="https://img.shields.io/badge/Release-1.1.0-5B5BD6">
  <img alt="Schema" src="https://img.shields.io/badge/DB%20Schema-23-orange">
</p>

---

## Overview

**Hira Model School Management System** is a single-school, offline-first desktop application developed in Python using **PySide6** and **SQLite**.

The application is designed for day-to-day school administration and supports the complete **April-to-March academic session**, including student admissions, class/section management, fee collection, promotions, expenses, income reporting, real-time finance, encrypted backups, restore/recovery, and software licensing.

The production application stores writable school data outside the installation directory under the Windows user's local application-data folder.

### Current Release

| Item | Value |
|---|---|
| Application | Hira Model School Management System |
| Release | **1.1.0** |
| Database schema | **23** |
| Source baseline | **V31** |
| GUI framework | **PySide6 6.11.1** |
| Database | **SQLite** |
| Target platform | **Windows 10/11, x64-compatible** |
| Academic session | **April → March** |
| Operation mode | **Offline desktop application** |

---

## Core Features

### Student Management

- Add new students
- Search and review student records
- Edit student information
- Automatic admission-number handling
- Roll-number management
- Class and section assignment
- Student-status management
- Dropped-student handling
- Reactivation
- Graduation and archival states
- Coming-session admissions
- Father/contact/CNIC information
- School Leaving Certificate-related handling
- Class-history tracking
- Sibling-related fee handling

Supported student states include:

- `Active`
- `Dropped`
- `Partially Graduated`
- `Graduated`
- `Archived`
- `Coming Session`

---

### Class Management

The system supports:

- Play Group
- Nursery
- KG
- Classes 1 to 10
- Multiple alphabetical sections
- Class capacity management
- Active/inactive class handling
- Class search and roster views
- Roll-number lifecycle control
- Coming-session capacity planning

Section names use uppercase alphabetic identifiers such as:

```text
A
B
C
...
Z
AA
AB
...
```

---

### Fee Management

The fee subsystem includes:

- Monthly class fee setup
- Individual student fee overrides
- Sibling fee configuration
- Fee payment
- Fee status by class
- Full-school fee list
- Month-range fee reporting
- Paid/unpaid tracking
- Receipt generation
- Payment cancellation controls
- Historical fee preservation
- April-to-March session-aware fee logic

Main fee screens include:

```text
Pay Fee
Class Status
Set Fee
Full School List
Sort by Month
```

The system distinguishes between:

1. **Class-level fee**
2. **Individual student fee**
3. **Sibling-policy fee**

Business rules preserve already-paid historical records when later fee settings change.

---

### Promotion Management

Promotion logic supports the academic sequence:

```text
PLAY GROUP
    ↓
NURSERY
    ↓
KG
    ↓
1
    ↓
2
    ↓
...
    ↓
10
    ↓
GRADUATION
```

Key capabilities include:

- Full-class promotion
- Pass/fail decisions
- Capacity validation
- Destination-class validation
- Roll-number regeneration
- Class-history records
- Fee-transition handling
- Class 10 graduation handling
- Academic-year transition safeguards

The standard session runs from **April through March**.

---

### Expenses Management

The application supports:

- Add expense
- Search previous expenses
- Date-aware financial records
- Expense reporting
- Integration with income and real-time finance calculations

---

### Income Management

Income Management provides:

- Session selection
- Month filtering
- Total collected fee
- Total expenses
- Final income
- Monthly fee-vs-expense comparison
- View report
- Print report
- Download PDF report

The main calculation is:

```text
Final Income = Total Collected Fee - Total Expenses
```

---

### Real-Time Finance

Real-Time Finance provides day-level financial visibility:

- Select exact date
- View today's activity
- Total fee collected
- Total expenses
- Final income
- Payment detail table
- Expense detail table
- Daily report
- Print support
- PDF export

---

### Statistics

The Statistics module provides a read-only academic-session overview including:

- Current school strength
- Session admissions
- Fee collected
- Total expenses
- Net balance
- Student-status distribution
- Monthly fee collection
- Monthly expenses

---

### Backup & Restore

The backup subsystem supports:

- Internal automatic backups
- User-selected backup folder
- Google Drive for Desktop folder integration
- Encrypted backup containers
- Manual backup creation
- Automatic backup scheduling
- Backup retention
- Backup validation
- Staged restore
- Reverse last restore
- Corrupt-backup rejection
- Restore atomicity
- Portable recovery-password support

Backup encryption uses modern cryptographic primitives including:

- **AES-256-GCM**
- **Scrypt**
- **HKDF**
- SHA-256 integrity verification
- Windows-protected local key material where applicable

> The recovery password is important. Losing it can prevent recovery of encrypted backups on another computer.

---

### Authentication

The application uses a single administrator identity.

Features include:

- First-run administrator setup
- Username and password authentication
- Password confirmation
- Recovery identity
- Recovery CNIC
- Date-of-birth verification
- Forgot-password workflow
- Login-attempt limiting

Password verification is based on salted **PBKDF2-HMAC** password derivation rather than storing plaintext passwords.

---

### Software Licensing

The application includes machine-bound software activation.

The licence subsystem uses:

- Digitally signed licence data
- **Ed25519** signatures
- Machine fingerprinting
- Public-key verification before school database access
- Permanent or controlled validity periods
- Private provider-only activation workflow

Licence verification occurs **before the school database is opened**.

> Activation credentials and private signing material are provider-controlled information and must never be placed in documentation, issues, commit messages, screenshots, or public repository content.

---

## Application Startup Sequence

At startup, the application performs a protected sequence:

```text
Application Start
      │
      ▼
Single-Instance Check
      │
      ▼
Licence Verification
      │
      ├── Invalid/Missing ──► Authorised Activation
      │
      ▼
Pending Restore Processing
      │
      ▼
Database Initialization / Migration
      │
      ▼
Coming-Session Activation
      │
      ▼
Automatic Backup Scheduler
      │
      ▼
Administrator Setup / Login
      │
      ▼
Main Dashboard
```

This sequence is intentionally ordered so that licensing, restore safety, schema validation, and authentication occur before normal school operations.

---

## Technology Stack

| Component | Technology |
|---|---|
| Programming language | Python 3.12 |
| Desktop GUI | PySide6 |
| Database | SQLite |
| PDF/report generation | ReportLab |
| Image handling | Pillow |
| Cryptography | `cryptography` |
| Packaging | PyInstaller |
| Windows installer | Inno Setup |
| Automated testing | Python `unittest` |
| Source control | Git / GitHub |

Pinned Python dependencies are stored in:

```text
requirements.txt
```

---

## Repository Structure

```text
SchoolManagementSystem/
│
├── app/
│   ├── assets/
│   ├── licence_generator/
│   ├── modules/
│   ├── services/
│   ├── auth_dialogs.py
│   ├── config.py
│   ├── database.py
│   ├── main_window.py
│   ├── navigation.py
│   ├── normalization.py
│   ├── schema_integrity.py
│   ├── styles.qss
│   ├── ui_shell.py
│   ├── ui_tables.py
│   └── ui_theme.py
│
├── assets/
│   └── images/
│
├── docs/
│   ├── FIXES_3_TO_8_REPORT.md
│   └── REGRESSION_TESTS.md
│
├── installer/
│   └── HiraModelSchoolInstaller.iss
│
├── tests/
│   ├── integration/
│   ├── regression/
│   └── unit/
│
├── tools/
│   ├── deployment_preflight.py
│   ├── run_all_tests.py
│   └── run_regression_tests.py
│
├── .gitignore
├── HiraModelSchool.spec
├── requirements.txt
├── run.py
└── SOURCE_PACKAGE_MANIFEST.json
```

---

## Architecture

The application follows a layered desktop architecture:

```text
┌─────────────────────────────────────┐
│             PySide6 UI              │
│ dialogs / pages / dashboards        │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│           Service Layer             │
│ student / class / fee / promotion   │
│ backup / auth / licence / finance   │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│      Database & Integrity Layer     │
│ migrations / transactions / SQL    │
│ triggers / schema safeguards        │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│               SQLite                │
│             school.db               │
└─────────────────────────────────────┘
```

### UI Layer

Located primarily in:

```text
app/modules/
app/main_window.py
app/auth_dialogs.py
app/ui_shell.py
```

### Business/Service Layer

Located in:

```text
app/services/
```

Examples:

```text
student_service.py
class_service.py
fee_service.py
promotion_service.py
backup_service.py
auth_service.py
license_service.py
income_service.py
expense_service.py
statistics_service.py
```

### Data & Integrity Layer

Core files:

```text
app/database.py
app/schema_integrity.py
```

The current database schema version is:

```text
23
```

---

## Runtime Data Locations

Writable production data is deliberately stored outside the installed program directory.

On Windows:

```text
%LOCALAPPDATA%\HiraModelSchool\
```

Main locations:

```text
%LOCALAPPDATA%\HiraModelSchool\data\school.db
%LOCALAPPDATA%\HiraModelSchool\backups\
%LOCALAPPDATA%\HiraModelSchool\exports\
```

The installed application files are separate from the live school database.

### Important

Do **not**:

- manually edit `school.db`;
- copy only `school.db` while the application is running;
- delete WAL/SHM files manually;
- replace application-data files with files from another installation;
- modify database schema using external SQLite tools;
- change the Windows clock backward to manipulate financial or academic records.

Use the application's backup/restore workflow for data movement.

---

## Development Setup

### Requirements

Recommended development environment:

- Windows 10 or Windows 11
- Python 3.12
- Git
- `pip`
- Optional: Inno Setup for installer compilation

Clone the repository:

```bash
git clone <your-repository-url>
cd SchoolManagementSystem
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate it in Windows Command Prompt:

```bat
venv\Scripts\activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Run the application:

```bash
python run.py
```

> A valid software licence is required before the application will open the school database. Provider activation procedures are intentionally not documented in this README.

---

## Running Tests

The project contains unit, integration, and regression tests.

Run the complete isolated test suite from the project root:

```bash
python tools/run_all_tests.py
```

Run the regression suite only:

```bash
python tools/run_regression_tests.py
```

The test runner redirects application data to temporary locations and is designed not to use the installed live school database.

Regression coverage includes areas such as:

- fee changes and historical preservation;
- payment cancellation;
- promotions and pass/fail handling;
- class capacity;
- roll-number lifecycle;
- student statuses;
- coming-session admissions;
- encrypted backup and restore;
- reverse restore;
- database migrations;
- startup hardening;
- clock rollback protection;
- schema-integrity safeguards;
- reporting and printing.

---

## Deployment Preflight

Before building a production release, run:

```bash
python tools/deployment_preflight.py
```

The preflight checks include:

- required application files;
- application version;
- database schema version;
- installer configuration;
- stale-install cleanup;
- accidental database/backup files in deployment payload;
- Python source compilation;
- suspicious future timestamps;
- provider-side signing-key deployment approval.

Any `ERROR` result should be resolved before release.

An `APPROVAL_REQUIRED` result must be consciously reviewed by the software owner.

---

## Building the Windows Application

### 1. Build with PyInstaller

From the project root, rebuild both Windows executables with:

```bat
tools\rebuild_windows.cmd
```

This creates the customer application at `dist\HiraModelSchool\HiraModelSchool.exe` and the separate owner-only licence generator at `owner_output\HiraModelSchoolLicenseGenerator\HiraModelSchoolLicenseGenerator.exe`.

To build only the customer application manually:

```bash
pyinstaller --clean HiraModelSchool.spec
```

Expected output:

```text
dist\HiraModelSchool\
```

The PyInstaller specification packages the application, Qt dependencies, styles, icons, logos, public licence key, and required runtime assets.

The owner-only generator must be built separately with `HiraModelSchoolLicenseGenerator.spec`; it is deliberately excluded from the customer application and installer because it contains the encrypted private signing key.

---

### 2. Build the Installer

After the PyInstaller build succeeds, compile:

```text
installer\HiraModelSchoolInstaller.iss
```

with Inno Setup.

Command-line example when `ISCC.exe` is available in `PATH`:

```bat
ISCC installer\HiraModelSchoolInstaller.iss
```

Expected installer filename:

```text
HiraModelSchoolSetup_1.1.0.exe
```

The installer targets an x64-compatible Windows installation and uses per-user installation under Local AppData.

---

## Database Migrations

Database migration is handled by application startup.

The application:

1. reads the existing SQLite schema version;
2. rejects unsupported newer schemas;
3. performs required migrations;
4. validates the resulting schema;
5. updates `PRAGMA user_version`;
6. opens normal application operations only after successful initialization.

Current required schema:

```text
23
```

Developers should never manually increment the schema version without implementing and testing the corresponding migration.

---

## Academic Session Model

The system uses an **April-to-March** academic year.

Example:

```text
2026-2027 session

April 2026
May 2026
June 2026
...
December 2026
January 2027
February 2027
March 2027
```

January, February, and March therefore belong to the session that began in the previous calendar year.

This session model is used across:

- fees;
- admissions;
- reporting;
- statistics;
- promotions;
- coming-session admissions;
- financial summaries.

---

## Operational Safety Principles

The application is designed around several high-integrity principles:

### Historical records are protected

Later fee or class changes must not silently rewrite already-paid historical transactions.

### Multi-record changes use transactions

Operations such as class-level fee updates, promotion, restore, and related cascades are designed to fail atomically rather than leave partial data.

### Restore is staged

A selected backup is validated before replacing production data.

### Live data is separated from program files

Application upgrades should not overwrite the school database.

### Startup validates the environment

Licence, restore state, schema, session transitions, and backup readiness are checked before normal use.

---

## Security Notes

### Do not commit runtime data

The repository `.gitignore` excludes:

```text
*.db
*.db-wal
*.db-shm
*.sqlite
*.log
.env
*.zip
build/
dist/
venv/
```

Never commit:

- real student information;
- production databases;
- fee/payment data;
- backup files;
- recovery passwords;
- administrator passwords;
- licence activation passwords;
- decrypted signing keys;
- customer-specific licence files.

### Important repository visibility warning

This codebase contains provider-side licence-generation components.

If this repository is hosted on GitHub, it should remain **private** unless the licensing architecture has first been redesigned for public-source distribution and all provider-controlled secrets/key material have been removed and rotated as necessary.

Do not rely on encryption of a committed private-key container as the sole reason to publish provider-side activation assets.

---

## Source Package Manifest

`SOURCE_PACKAGE_MANIFEST.json` records the maintained source baseline, including file hashes and release metadata.

Current manifest baseline:

```text
Application version: 1.1.0
Patch baseline: V31
Database schema: 23
```

The manifest can be used when verifying that a deployment source tree matches an approved source snapshot.

---

## Documentation

Repository documentation currently includes:

```text
docs/FIXES_3_TO_8_REPORT.md
docs/REGRESSION_TESTS.md
```

Additional project documentation maintained outside the source tree may include:

- Software Requirements Specification (SRS)
- Technical Design & Architecture Document
- Source Code Technical Reference
- Business Rules & Change-Impact Manual
- End-User Operations Manual
- Technician Installation & Commissioning Manual

---

## Contribution / Change Control

This is a production school-management application. Changes should not be merged directly into the release branch without verification.

Recommended workflow:

```text
Create branch
     ↓
Implement change
     ↓
Run isolated tests
     ↓
Add regression coverage
     ↓
Run deployment preflight
     ↓
Review database/business-rule impact
     ↓
Merge
     ↓
Build clean release
```

For changes affecting:

- database schema;
- fee calculation;
- payments;
- promotions;
- student status;
- backups/restores;
- licensing;
- academic-session transitions;

a regression test should be added or updated before release.

---

## Release Checklist

Before distributing a new version:

- [ ] Update application/installer version where required
- [ ] Confirm database schema version
- [ ] Run `python tools/run_all_tests.py`
- [ ] Run `python tools/deployment_preflight.py`
- [ ] Resolve all deployment errors
- [ ] Review all approval-required security items
- [ ] Build with a clean PyInstaller output
- [ ] Compile the Inno Setup installer
- [ ] Test first installation
- [ ] Test upgrade over the previous release
- [ ] Test licence verification
- [ ] Test administrator login
- [ ] Test database initialization/migration
- [ ] Create and verify a backup
- [ ] Test restore on isolated data
- [ ] Confirm no real school database is bundled
- [ ] Confirm no plaintext credentials are included

---

## Project Status

The repository represents the maintained **Hira Model School Management System v1.1.0 / schema 23 / V31 source baseline**.

The system is designed for controlled deployment to Hira Model School as an offline Windows desktop application.

---

## Licence / Ownership

This repository does **not** currently contain an open-source `LICENSE` file.

Unless the repository owner publishes an explicit open-source licence, the source code should be treated as **private/proprietary software** and should not be copied, redistributed, sold, sublicensed, or publicly deployed without the owner's authorization.

---

## Support

For technical support, provide the technician with:

- application version;
- Windows version;
- exact error message;
- operation being performed;
- whether the problem occurs before or after login;
- whether a recent backup exists;
- relevant crash report where available.

Do **not** send passwords, recovery passwords, private activation credentials, or live databases through public GitHub issues.

---

<p align="center">
  <strong>Hira Model School Management System</strong><br>
  Offline • Controlled • Backup-Aware • Session-Aware
</p>
