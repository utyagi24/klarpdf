; Inno Setup script for KlarPDF (PLAN.md, Packaging §4).
;
; Per-user install (no admin): bundles the PyInstaller --onedir tree, writes the HKCU .pdf ProgID +
; Open-With association, a Start-Menu shortcut, and an uninstaller that removes the app, the
; registry keys, AND %LOCALAPPDATA%\klarpdf. Compile from the repo root after dist\klarpdf\ exists:
;   ISCC /DMyAppVersion=0.1.0 packaging\installer.iss
; build.ps1 passes the version from version.py.
;
; Two spellings, deliberately (assets\brand\BRAND.md §Type, PROGRESS.md §G2): MyAppName is the
; display string Windows renders in its own font (Add/Remove Programs, shortcuts, Open With);
; MyAppSlug is the lowercase identifier used for paths and filenames.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "KlarPDF"
#define MyAppSlug "klarpdf"
#define MyAppExe "klarpdf.exe"
#define MyAppProgId "KlarPDF.Document"
#define MyAppDocIco "klarpdf-doc.ico"

[Setup]
; AppId is the installation's identity — Inno matches on it, NOT on AppName. This GUID was minted
; fresh at the pdfproj -> KlarPDF rename so the renamed setup is a NEW app, not an in-place upgrade
; of pdfproj: an upgrade would silently skip the old uninstaller's ProgID / OpenWithProgids cleanup
; and reuse pdfproj's recorded install directory. Uninstall pdfproj first (RELEASE.md).
; Never change this GUID again — doing so orphans every existing install.
AppId={{7E66AD28-218E-4488-AA66-2795D9F1A1B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename={#MyAppSlug}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#MyAppSlug}.ico
UninstallDisplayIcon={app}\{#MyAppExe}

[Files]
Source: "..\dist\{#MyAppSlug}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; The `.pdf` DOCUMENT icon, referenced by the ProgID DefaultIcon below. Not embedded in the exe —
; the exe carries the APPLICATION icon, and a document is not the program that opens it.
Source: "{#MyAppDocIco}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Registry]
; Per-user ProgID under HKCU\Software\Classes (no admin). uninsdeletekey removes the whole subtree.
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}"; ValueType: string; ValueName: ""; ValueData: "PDF Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"
; A `.pdf` file gets the DOCUMENT icon, not the application icon. Before v0.10.1 this pointed at
; "{app}\klarpdf.exe,0", so every PDF on disk wore KlarPDF's own app icon.
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppDocIco},0"
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""
; Add KlarPDF to the .pdf "Open With" list (does NOT claim default; the user confirms that once).
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppProgId}"; ValueData: ""; Flags: uninsdeletevalue

[UninstallDelete]
; Clean removal of the per-user runtime config (the view-state JSON written by store/settings.py).
; NOTE: {localappdata}, not {userappdata}. Qt's QStandardPaths.AppConfigLocation resolves to
; %LOCALAPPDATA% on Windows, not %APPDATA% (Roaming) — verified at runtime. The pdfproj-era script
; deleted {userappdata}\pdfproj, a path that never existed, so its config was never cleaned up.
Type: filesandordirs; Name: "{localappdata}\{#MyAppSlug}"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
