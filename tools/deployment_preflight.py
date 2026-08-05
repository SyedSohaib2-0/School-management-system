#!/usr/bin/env python3
"""Read-only deployment preflight for Hira Model School V31."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_APPLICATION_VERSION = "1.1.0"
EXPECTED_SCHEMA_VERSION = 23
FORBIDDEN_RUNTIME_SUFFIXES = (".db", ".db-wal", ".db-shm")
EXCLUDED_PARTS = {
    ".git", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "__pycache__", "venv", ".venv", "build",
}


def _record(results: list[dict], level: str, code: str, message: str) -> None:
    results.append({"level": level, "code": code, "message": message})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _iter_deployment_files(base: Path):
    if not base.exists():
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        yield path


def run_preflight() -> dict:
    results: list[dict] = []
    required = [
        ROOT / "run.py",
        ROOT / "app" / "database.py",
        ROOT / "app" / "license_public_key.pem",
        ROOT / "app" / "styles.qss",
        ROOT / "app" / "assets" / "hira_model_school_icon.ico",
        ROOT / "HiraModelSchool.spec",
        ROOT / "installer" / "HiraModelSchoolInstaller.iss",
    ]
    for path in required:
        if path.is_file():
            _record(results, "pass", "required_file", f"Found {path.relative_to(ROOT)}")
        else:
            _record(results, "error", "required_file_missing", f"Missing {path.relative_to(ROOT)}")

    database_path = ROOT / "app" / "database.py"
    if database_path.is_file():
        match = re.search(r"CURRENT_SCHEMA_VERSION\s*=\s*(\d+)", _read(database_path))
        actual = int(match.group(1)) if match else None
        if actual == EXPECTED_SCHEMA_VERSION:
            _record(results, "pass", "schema_version", f"Schema version is {actual}")
        else:
            _record(results, "error", "schema_version", f"Expected schema {EXPECTED_SCHEMA_VERSION}, found {actual}")

    installer_path = ROOT / "installer" / "HiraModelSchoolInstaller.iss"
    if installer_path.is_file():
        installer = _read(installer_path)
        match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', installer)
        actual = match.group(1) if match else None
        if actual == EXPECTED_APPLICATION_VERSION:
            _record(results, "pass", "application_version", f"Application version is {actual}")
        else:
            _record(results, "error", "application_version", f"Expected {EXPECTED_APPLICATION_VERSION}, found {actual}")
        if "[InstallDelete]" in installer and 'Name: "{app}\\*"' in installer:
            _record(results, "pass", "stale_install_cleanup", "Installer removes stale program files before upgrade")
        else:
            _record(results, "error", "stale_install_cleanup", "Installer lacks stale program-file cleanup")

    spec_path = ROOT / "HiraModelSchool.spec"
    if spec_path.is_file():
        spec = _read(spec_path)
        if "encrypted_signing_key.json" in spec:
            _record(
                results,
                "approval_required",
                "customer_signing_key",
                "The customer build bundles the encrypted private licence-signing key and in-app generator. This security architecture needs owner approval before deployment.",
            )

    forbidden = []
    for base in (ROOT, ROOT / "dist"):
        for path in _iter_deployment_files(base) or ():
            name = path.name.lower()
            if name.endswith(FORBIDDEN_RUNTIME_SUFFIXES) or name.endswith(".hmsbackup"):
                # Test fixtures belong under tests and are not customer payloads.
                try:
                    relative = path.relative_to(ROOT)
                except ValueError:
                    relative = path
                if "tests" not in relative.parts:
                    forbidden.append(str(relative))
    if forbidden:
        _record(results, "error", "runtime_data_in_package", "Runtime data found in source/build: " + ", ".join(sorted(set(forbidden))[:20]))
    else:
        _record(results, "pass", "runtime_data_in_package", "No live database, WAL/SHM, or backup payload found")

    future_limit = datetime.now(timezone.utc) + timedelta(hours=24)
    future_files = []
    for path in _iter_deployment_files(ROOT) or ():
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified > future_limit:
            future_files.append(path.relative_to(ROOT).as_posix())
    if future_files:
        _record(
            results,
            "warning",
            "future_timestamps",
            f"{len(future_files)} source file(s) have future timestamps; run a clean PyInstaller build. Examples: " + ", ".join(future_files[:8]),
        )
    else:
        _record(results, "pass", "future_timestamps", "No materially future-dated source files")

    # Compile source text in memory. A deployment preflight is read-only and
    # must not create __pycache__ directories or .pyc files that can leak into
    # a later source/archive build.
    compile_failures = []
    python_sources = [ROOT / "run.py"]
    python_sources.extend(sorted((ROOT / "app").rglob("*.py")))
    for source_path in python_sources:
        try:
            source_text = source_path.read_text(encoding="utf-8")
            compile(source_text, str(source_path), "exec")
        except (OSError, UnicodeError, SyntaxError) as error:
            compile_failures.append(
                f"{source_path.relative_to(ROOT)}: {type(error).__name__}: {error}"
            )
    if not compile_failures:
        _record(
            results,
            "pass",
            "python_compile",
            "Application Python sources compile successfully without writing bytecode",
        )
    else:
        _record(
            results,
            "error",
            "python_compile",
            "One or more Python files failed compilation: "
            + "; ".join(compile_failures[:10]),
        )

    errors = [item for item in results if item["level"] == "error"]
    warnings = [item for item in results if item["level"] in {"warning", "approval_required"}]
    return {
        "application_version": EXPECTED_APPLICATION_VERSION,
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "results": results,
    }


def main() -> int:
    report = run_preflight()
    artifact_directory = ROOT.parent / "HMS_V31_Deployment_Preflight_Artifacts"
    artifact_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = artifact_directory / f"deployment_preflight_report_{stamp}.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for item in report["results"]:
        print(f"[{item['level'].upper()}] {item['code']}: {item['message']}")
    print(f"Report: {output_path}")
    if report["passed"]:
        print(f"=== DEPLOYMENT PREFLIGHT PASSED ({report['warning_count']} warning/approval item(s)) ===")
        return 0
    print(f"=== DEPLOYMENT PREFLIGHT FAILED ({report['error_count']} error(s)) ===")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
