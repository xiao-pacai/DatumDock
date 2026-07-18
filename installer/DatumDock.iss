; DatumDock 的 Windows 安装定义。安装包未签名，发布说明须提醒 SmartScreen 提示。

#define AppName "DatumDock"
#define AppVersion "0.1.0"
#define AppPublisher "DatumDock Contributors"
#define AppExeName "DatumDock.exe"

[Setup]
AppId={{CB451963-376D-4057-962B-7B4D6D8B149B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\DatumDock
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=DatumDock-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\assets\brand\datumdock-app-icon.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\DatumDock\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\DatumDock"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\DatumDock"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,DatumDock}"; Flags: nowait postinstall skipifsilent
