; Inno Setup script for the Windows installer (Inno Setup 6).
; The desktop-windows workflow builds it from the PyInstaller folder:
;     iscc /DAppVersion=1.3.0 installer\orpheus.iss      (run in desktop\)
; → dist\OrpheusSetup-1.3.0.exe
;
; Per-user install: no administrator prompt, files in
; %LOCALAPPDATA%\Programs\Orpheus, nothing written for other users.

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=x.y.z installer\orpheus.iss
#endif
#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\dist\OrpheusDesktop"
#endif
#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\dist"
#endif
#define IconFile AddBackslash(SourcePath) + "..\build\orpheus.ico"

[Setup]
AppId={{C4F20703-EC4E-411C-8DFD-A9C097EDEEE5}
AppName=Orpheus
AppVersion={#AppVersion}
AppVerName=Orpheus {#AppVersion}
AppPublisher=CocaKova
AppPublisherURL=https://github.com/CocaKova/orpheus
AppSupportURL=https://github.com/CocaKova/orpheus/issues
AppUpdatesURL=https://github.com/CocaKova/orpheus/releases
DefaultDirName={autopf}\Orpheus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=OrpheusSetup-{#AppVersion}
#if FileExists(IconFile)
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\OrpheusDesktop.exe
UninstallDisplayName=Orpheus
InfoBeforeFile=before-install.txt
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Orpheus is stopped by PrepareToInstall below, so Restart Manager need not ask.
CloseApplications=no
VersionInfoProductName=Orpheus
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Orpheus setup
VersionInfoCompany=CocaKova
VersionInfoCopyright=Copyright (c) 2026 CocaKova. MIT License.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start Orpheus when I sign in to Windows"; GroupDescription: "Startup:"

[InstallDelete]
; an upgrade replaces the runtime folder whole, so no stale module survives
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Orpheus"; Filename: "{app}\OrpheusDesktop.exe"; Comment: "Push-to-talk dictation"
Name: "{autoprograms}\Orpheus settings"; Filename: "{app}\OrpheusDesktop.exe"; Parameters: "settings"; Comment: "Server, hotkey and text options"

[Registry]
; The same value the app's own "Start with Windows" switch writes, so either
; one can turn it off again (and Task Manager > Startup apps lists it).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Orpheus"; ValueData: """{app}\OrpheusDesktop.exe"""; Tasks: startup

[Run]
; An Orpheus started from somewhere else (the old one-file exe) holds the
; control port; ask it to quit so the new one can start.
Filename: "{app}\OrpheusDesktop.exe"; Parameters: "quit"; Flags: runhidden waituntilterminated
Filename: "{app}\OrpheusDesktop.exe"; Description: "Start Orpheus now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; quit waits until the daemon has let go of its files; uninstall removes the
; startup entry and, if `OrpheusDesktop.exe install` added it, the H in
; Explorer's DisabledHotkeys (and nothing else there).
Filename: "{app}\OrpheusDesktop.exe"; Parameters: "quit"; RunOnceId: "OrpheusQuit"; Flags: runhidden waituntilterminated
Filename: "{app}\OrpheusDesktop.exe"; Parameters: "uninstall"; RunOnceId: "OrpheusUnregister"; Flags: runhidden waituntilterminated

[Code]
// Upgrading over a running Orpheus: stop it before its files are replaced.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Exe: String;
begin
  Result := '';
  Exe := ExpandConstant('{app}\OrpheusDesktop.exe');
  if FileExists(Exe) then
    Exec(Exe, 'quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
