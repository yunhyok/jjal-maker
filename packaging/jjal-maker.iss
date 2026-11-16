#ifndef AppVersion
  #error AppVersion must be supplied by scripts/build.ps1
#endif

[Setup]
AppId={{9E496BBA-33F1-4DC0-9F71-136F06093A5E}
AppName=Jjal Maker
AppVersion={#AppVersion}
AppPublisher=Jjal Maker contributors
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\Jjal Maker
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.22000
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
UninstallDisplayIcon={app}\JjalMaker.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
RestartApplications=no
OutputDir=..\dist\release
OutputBaseFilename=JjalMaker-{#AppVersion}-win-x64-setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "..\dist\JjalMaker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Jjal Maker"; Filename: "{app}\JjalMaker.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\JjalMaker.exe"; Description: "{cm:LaunchProgram,Jjal Maker}"; Flags: nowait postinstall skipifsilent
