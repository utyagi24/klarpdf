; Inno Setup script for pdfproj (PLAN.md, Packaging §4).
;
; Per-user install (no admin): bundles the PyInstaller --onedir tree, writes the HKCU .pdf ProgID +
; Open-With association, a Start-Menu shortcut, and an uninstaller that removes the app, the
; registry keys, AND %APPDATA%\pdfproj. Compile from the repo root after dist\pdfproj\ exists:
;   ISCC /DMyAppVersion=0.1.0 packaging\installer.iss
; build.ps1 passes the version from version.py.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "pdfproj"
#define MyAppExe "pdfproj.exe"

[Setup]
AppId={{7FC0B9A9-6FE3-4EEB-BE6E-83F4A00D4E8B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=pdfproj
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=pdfproj-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=pdfproj.ico
UninstallDisplayIcon={app}\{#MyAppExe}

[Files]
Source: "..\dist\pdfproj\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Registry]
; Per-user ProgID under HKCU\Software\Classes (no admin). uninsdeletekey removes the whole subtree.
Root: HKCU; Subkey: "Software\Classes\pdfproj.Document"; ValueType: string; ValueName: ""; ValueData: "PDF Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\pdfproj.Document"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"
Root: HKCU; Subkey: "Software\Classes\pdfproj.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExe},0"
Root: HKCU; Subkey: "Software\Classes\pdfproj.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""
; Add pdfproj to the .pdf "Open With" list (does NOT claim default; the user confirms that once).
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "pdfproj.Document"; ValueData: ""; Flags: uninsdeletevalue

[UninstallDelete]
; Clean removal of the per-user runtime config (the view-state JSON written by store/settings.py).
Type: filesandordirs; Name: "{userappdata}\pdfproj"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch pdfproj"; Flags: nowait postinstall skipifsilent
