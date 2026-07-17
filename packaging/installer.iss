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
; Only architecture built today (win_amd64-pinned wheels, windows-latest x64 runner). Carried in
; the *released artifact's* filename (OutputBaseFilename below) so a future arm64 build can't
; collide with or be mistaken for this one. The installed exe name (MyAppExe) is untouched — it's
; not a filename anyone chooses between on the Releases page, and platform_integration's mutex/
; ProgID wiring keys off it, so renaming it is a separate, larger change than this one.
#define MyAppArch "x64"
#define MyAppExe "klarpdf.exe"
#define MyAppProgId "KlarPDF.Document"
#define MyAppDocIco "klarpdf-doc.ico"
; MUST match platform_integration.APP_MUTEX_NAME. tests/test_app_mutex.py asserts they never drift:
; a rename on either side silently disables the guard below, and nothing else would notice.
#define MyAppMutex "KlarPDF-AppMutex"

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
OutputBaseFilename={#MyAppSlug}-setup-{#MyAppArch}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#MyAppSlug}.ico
UninstallDisplayIcon={app}\{#MyAppExe}

; Refuse to install OR uninstall while KlarPDF is running. The app holds this named mutex for its
; whole lifetime (platform_integration.acquire_app_mutex); Setup and the uninstaller both test for it
; and ask the user to close the app. Without this, at v0.10.0:
;   * Windows would not let the uninstaller delete the running .exe, so the install directory
;     survived (a per-user install cannot even queue a reboot-time delete), and
;   * [UninstallDelete] removed %LOCALAPPDATA%\klarpdf, then the still-live process wrote
;     view_state.json on shutdown and recreated it.
; Neither is a packaging bug; both vanish if the app simply isn't running. See RELEASE.md.
AppMutex={#MyAppMutex}
; Refuse, don't auto-close. Restart Manager could close the app for us, but KlarPDF prompts on
; unsaved edits and a forced close would bypass that prompt. Losing a user's annotations to make an
; installer more convenient is the wrong trade.
CloseApplications=no
RestartApplications=no
; And never let two Setups run at once.
SetupMutex={#MyAppMutex}-Setup

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
