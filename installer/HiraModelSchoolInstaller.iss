#define MyAppName "Hira Model School Management System"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Hira Model School"
#define MyAppExeName "HiraModelSchool.exe"

[Setup]
AppId={{C149E172-170B-4E64-A762-192B8423BD34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}

DefaultDirName={localappdata}\Programs\Hira Model School Management System
DefaultGroupName=Hira Model School
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\installer_output
OutputBaseFilename=HiraModelSchoolSetup_1.1.0

SetupIconFile=..\app\assets\hira_model_school_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

MinVersion=10.0
CloseApplications=yes
CloseApplicationsFilter=HiraModelSchool.exe
RestartApplications=no
SetupLogging=yes

CreateUninstallRegKey=yes
Uninstallable=yes

VersionInfoVersion=1.1.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoOriginalFileName=HiraModelSchoolSetup_1.1.0.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[InstallDelete]
; Program data and the licence live under LocalAppData\HiraModelSchool, not here.
; Clean stale binaries from an older one-folder build before copying the new build.
Type: filesandordirs; Name: "{app}\*"

[Files]
Source: "..\dist\HiraModelSchool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Hira Model School Management System"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Hira Model School Management System"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Hira Model School Management System"; Flags: nowait postinstall skipifsilent
