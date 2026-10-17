; Inno Setup recipe for EditForge-Setup.exe. Build with:  iscc /DMyVersion=1.4.0 packaging\installer.iss
; It installs for the current user only, so Windows does not ask for an administrator password.
#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
AppId={{6E2C1B4A-5D2B-4C69-9E57-ED17F0F0A1B5}
AppName=EditForge
AppVersion={#MyVersion}
AppVerName=EditForge {#MyVersion}
AppPublisher=EditForge
AppPublisherURL=https://github.com/nyenz/Video-editing-
DefaultDirName={localappdata}\Programs\EditForge
DefaultGroupName=EditForge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=EditForge-Setup
SetupIconFile=editforge.ico
UninstallDisplayIcon={app}\EditForge.exe
UninstallDisplayName=EditForge
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put an EditForge icon on the desktop"; GroupDescription: "Icons:"

[Files]
Source: "..\dist\EditForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\EditForge"; Filename: "{app}\EditForge.exe"; Comment: "Edit videos on this computer"
Name: "{autodesktop}\EditForge"; Filename: "{app}\EditForge.exe"; Tasks: desktopicon; Comment: "Edit videos on this computer"

[Run]
Filename: "{app}\EditForge.exe"; Description: "Open EditForge now"; Flags: nowait postinstall skipifsilent
