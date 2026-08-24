@echo off
setlocal

cd /d "%~dp0.."
set "PYTHON=venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Python environment not found: %PYTHON%
    exit /b 1
)

echo Building customer application...
"%PYTHON%" -m PyInstaller --clean --noconfirm --distpath dist --workpath build\customer HiraModelSchool.spec
if errorlevel 1 exit /b %errorlevel%

echo Building owner-only licence generator...
"%PYTHON%" -m PyInstaller --clean --noconfirm --distpath owner_output --workpath build\owner HiraModelSchoolLicenseGenerator.spec
if errorlevel 1 exit /b %errorlevel%

echo.
echo Customer application:
echo   dist\HiraModelSchool\HiraModelSchool.exe
echo Owner-only licence generator:
echo   owner_output\HiraModelSchoolLicenseGenerator\HiraModelSchoolLicenseGenerator.exe
echo.
echo Keep the owner_output folder restricted to authorised licence administrators.
exit /b 0