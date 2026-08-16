; Customer Success Hub — Windows installer
;
; Per-user install: the app itself needs no administrator rights and goes into
; %LOCALAPPDATA%\Programs. Registering the Windows service does need admin, so
; that one step elevates on its own (a single UAC prompt) rather than forcing
; the whole installer to run elevated.
;
; Build with installer\build.ps1, which stages the payload and passes:
;   /DAppVersion=1.0.0 /DPayloadDir=...\.build\payload /DOutputDir=...\dist
;
; Requires Inno Setup 6.2 or newer.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef PayloadDir
  #define PayloadDir ".build\payload"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif

#define AppName        "Customer Success Hub"
#define AppShortName   "CustomerSuccessHub"
#define AppPublisher   "Customer Success"
#define DefaultPort    "8300"

[Setup]
AppId={{8F3B6E21-2C7A-4E5D-9A64-1B0C7D5E4A12}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#AppShortName}-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; "x64compatible" is the modern spelling (Inno Setup 6.3+); "x64" keeps older
; compilers working. Both mean: 64-bit Windows only, install in 64-bit mode.
#if VER >= EncodeVer(6,3,0)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#else
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#endif
PrivilegesRequired=lowest
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\runtime\python.exe
MinVersion=10.0
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "service"; Description: "Run as a Windows service, starting with the machine (asks for administrator once)"; GroupDescription: "How it runs:"
Name: "firewall"; Description: "Allow other machines on the network to reach it (firewall rule)"; GroupDescription: "How it runs:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\bin\open.cmd";        IconFilename: "{app}\runtime\python.exe"; Comment: "Open the console in your browser"
Name: "{group}\Run in this window";   Filename: "{app}\bin\run-console.cmd"; IconFilename: "{app}\runtime\python.exe"; Comment: "Run the server in a console window instead of the service"
Name: "{group}\Data folder";          Filename: "{code:DataDir}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\bin\open.cmd";        IconFilename: "{app}\runtime\python.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\bin\open.cmd"; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent

[Code]
var
  OptionsPage: TInputQueryWizardPage;
  NetworkCheck: TNewCheckBox;

procedure InitializeWizard;
begin
  OptionsPage := CreateInputQueryPage(wpSelectTasks,
    'Server settings',
    'Where should the console listen, and where should its data live?',
    'Both can be changed later in server.env inside the data folder.');
  OptionsPage.Add('Port:', False);
  OptionsPage.Add('Data folder (databases, uploads, ticket exports):', False);
  OptionsPage.Values[0] := '{#DefaultPort}';
  OptionsPage.Values[1] := ExpandConstant('{localappdata}\{#AppShortName}');

  NetworkCheck := TNewCheckBox.Create(WizardForm);
  NetworkCheck.Parent := OptionsPage.Surface;
  NetworkCheck.Left := OptionsPage.Edits[1].Left;
  NetworkCheck.Top := OptionsPage.Edits[1].Top + OptionsPage.Edits[1].Height + ScaleY(14);
  NetworkCheck.Width := OptionsPage.SurfaceWidth;
  NetworkCheck.Height := ScaleY(17);
  NetworkCheck.Caption := 'Let colleagues on the network reach it (listen on 0.0.0.0)';
  NetworkCheck.Checked := False;
end;

function Port(Param: String): String;
begin
  Result := Trim(OptionsPage.Values[0]);
  if Result = '' then
    Result := '{#DefaultPort}';
end;

function DataDir(Param: String): String;
begin
  Result := Trim(OptionsPage.Values[1]);
  if Result = '' then
    Result := ExpandConstant('{localappdata}\{#AppShortName}');
end;

function ListenHost(Param: String): String;
begin
  if NetworkCheck.Checked then
    Result := '0.0.0.0'
  else
    Result := '127.0.0.1';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  PortNumber: Integer;
begin
  Result := True;
  if CurPageID = OptionsPage.ID then
  begin
    PortNumber := StrToIntDef(Trim(OptionsPage.Values[0]), -1);
    if (PortNumber < 1) or (PortNumber > 65535) then
    begin
      MsgBox('Please enter a port between 1 and 65535.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if Trim(OptionsPage.Values[1]) = '' then
    begin
      MsgBox('Please choose a data folder.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Everything the launchers and the service need to know, in one file that a
  human can edit afterwards without reinstalling. }
procedure WriteRuntimeConfig;
var
  Config: String;
begin
  Config :=
    '# Written by the installer. Edit and restart the service to change.' + #13#10 +
    'CSHUB_DATA_DIR=' + DataDir('') + #13#10 +
    'CSHUB_PRIVATE_RUNTIME=1' + #13#10 +
    'HOST=' + ListenHost('') + #13#10 +
    'PORT=' + Port('') + #13#10;
  ForceDirectories(DataDir(''));
  SaveStringToFile(DataDir('') + '\server.env', Config, False);
  { The uninstaller reads these back — the wizard pages are long gone by then. }
  SaveStringToFile(ExpandConstant('{app}\service\install.conf'),
                   'PORT=' + Port('') + #13#10 + 'DATA=' + DataDir('') + #13#10, False);
end;

{ Registering a service needs administrator. Ask for it here, for this step
  only, instead of elevating the whole installer. }
procedure RunElevated(Args: String; StepName: String);
var
  ResultCode: Integer;
begin
  if not ShellExec('runas', 'powershell.exe',
                   '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\bin\service.ps1') + '" ' + Args,
                   '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox(StepName + ' was skipped: administrator approval was not given.' + #13#10 +
           'You can do it later from the Start Menu folder, or run bin\service.ps1 as administrator.',
           mbInformation, MB_OK);
    Exit;
  end;
  if ResultCode <> 0 then
    MsgBox(StepName + ' failed (exit code ' + IntToStr(ResultCode) + ').' + #13#10 +
           'The app still works from the "Run in this window" shortcut.', mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;

  WriteRuntimeConfig;

  if WizardIsTaskSelected('service') then
    RunElevated('-Action install -InstallDir "' + ExpandConstant('{app}') + '"' +
                ' -DataDir "' + DataDir('') + '" -Port ' + Port('') + ' -ListenHost ' + ListenHost(''),
                'Installing the Windows service');

  if WizardIsTaskSelected('firewall') then
    RunElevated('-Action firewall -Port ' + Port(''), 'Adding the firewall rule');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  { Remove the service and firewall rule if they were ever created. The script
    is a no-op when they weren't, so a declined UAC prompt is harmless. }
  if FileExists(ExpandConstant('{app}\service\CustomerSuccessHubService.xml')) or
     FileExists(ExpandConstant('{app}\service\install.conf')) then
    ShellExec('runas', 'powershell.exe',
              '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\bin\service.ps1') +
              '" -Action uninstall -InstallDir "' + ExpandConstant('{app}') + '"',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
