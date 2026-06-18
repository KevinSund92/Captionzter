; CaptionStudio — Inno Setup installer script
; Build with:  build_installer.bat
;              (or manually: ISCC.exe installer\captionsudio.iss)
;
; Prerequisites:
;   - PyInstaller output in dist\CaptionStudio\
;   - Inno Setup 6 installed (https://jrsoftware.org/isinfo.php)

#define AppName      "CaptionStudio"
#define AppVersion   "1.0.5"
#define AppPublisher "CaptionStudio"
#define AppURL       "https://github.com/KevinSund92/Captionzter"
#define AppExeName   "CaptionStudio.exe"

[Setup]
AppId={{A7B3C2D1-4E5F-6789-ABCD-EF0123456789}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; No code signing — users will see an "Unknown publisher" SmartScreen warning
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=CaptionStudio_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Uncomment when you have an icon:
; SetupIconFile=..\assets\icons\app_icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";   Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove downloaded models and cached data on uninstall
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\bin"
