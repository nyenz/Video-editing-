; Inno Setup recipe for EditForge-Setup.exe. Build with:  iscc /DMyVersion=1.8.0 packaging\installer.iss
; It installs for the current user only, so Windows does not ask for an administrator password.
#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
AppId={{6E2C1B4A-5D2B-4C69-9E57-ED17F0F0A1B5}
AppName=Nyenz Edit
AppVersion={#MyVersion}
AppVerName=Nyenz Edit {#MyVersion}
AppPublisher=Nyenz Edit
AppPublisherURL=https://github.com/nyenz/Video-editing-
DefaultDirName={localappdata}\Programs\EditForge
DefaultGroupName=EditForge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=EditForge-Setup
SetupIconFile=editforge.ico
UninstallDisplayIcon={app}\EditForge.exe
UninstallDisplayName=Nyenz Edit (EditForge)
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put a Nyenz Edit icon on the desktop"; GroupDescription: "Icons:"

[InstallDelete]
; the icons were called "EditForge" before version 1.5.0
Type: files; Name: "{autoprograms}\EditForge.lnk"
Type: files; Name: "{autodesktop}\EditForge.lnk"

[Files]
Source: "..\dist\EditForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID matches the app window (editforge/taskbar.py), so the window and a pinned button use the Nyenz Edit icon
Name: "{autoprograms}\Nyenz Edit"; Filename: "{app}\EditForge.exe"; Comment: "Edit videos on this computer"; AppUserModelID: "NyenzEdit.App"
Name: "{autodesktop}\Nyenz Edit"; Filename: "{app}\EditForge.exe"; Tasks: desktopicon; Comment: "Edit videos on this computer"; AppUserModelID: "NyenzEdit.App"

[Run]
Filename: "{app}\EditForge.exe"; Description: "Open Nyenz Edit now"; Flags: nowait postinstall skipifsilent
; after an automatic update (the app runs the installer with /SILENT /RELAUNCH) open the app again
Filename: "{app}\EditForge.exe"; Flags: nowait runasoriginaluser; Check: Relaunch

[Code]
function Relaunch(): Boolean;
begin
  Result := WizardSilent() and (Pos('/RELAUNCH', Uppercase(GetCmdTail())) > 0);
end;
