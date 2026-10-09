; Inno Setup script for LogDigitizer 1.1.0
; Compile this file only after rebuilding dist\LogDigitizer with PyInstaller.

#define MyAppName "LogDigitizer"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "CN IGME-CSIC"
#define MyAppURL "https://github.com/CarlosMarinLechado/LogDigitizer"
#define MyAppExeName "LogDigitizer.exe"
#define MyAppGuideName "LogDigitizer_User_Guide.pdf"

[Setup]
AppId={{0985BB25-18F7-41A1-BE94-8CF71A10A3A5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) 2026 Carlos Marín Lechado
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\installer_output
OutputBaseFilename=LogDigitizer_Setup_{#MyAppVersion}
SetupIconFile=..\assets\LogDigitizer.ico
LicenseFile=..\LICENSE
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousLanguage=yes
VersionInfoVersion=1.1.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoOriginalFileName=LogDigitizer_Setup_{#MyAppVersion}.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The manual is installed at {app}\docs instead of being duplicated in _internal.
Source: "..\dist\LogDigitizer\*"; DestDir: "{app}"; Excludes: "_internal\docs\{#MyAppGuideName}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\{#MyAppGuideName}"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CITATION.cff"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\User Guide"; Filename: "{app}\docs\{#MyAppGuideName}"; WorkingDir: "{app}\docs"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent