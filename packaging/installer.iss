; Inno Setup Script for AI-kms Daemon (Windows)
;
; Prerequisites:
;   - Windows
;   - PyInstaller-built dist/ai-kms-daemon/ (from packaging/daemon.spec)
;   - Inno Setup 6 (https://jrsoftware.org/isinfo.php)
;
; Build:
;   iscc packaging/installer.iss
;
; Output: dist/AI-kms-Daemon-Setup.exe

[Setup]
AppName=AI-kms Daemon
AppVersion=0.1.0
DefaultDirName={pf}\AI-kms Daemon
DefaultGroupName=AI-kms Daemon
OutputDir=dist
OutputBaseFilename=AI-kms-Daemon-Setup
Compression=lzma2
SolidCompression=yes
Uninstallable=yes

[Files]
Source: "dist\ai-kms-daemon\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Run]
Filename: "{app}\ai-kms-daemon.exe"; Description: "Launch AI-kms Daemon"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\ai-kms-daemon.exe"; Parameters: "uninstall"; Flags: runhidden
