# Windows installer

Builds `CustomerSuccessHub-<version>-setup.exe`: a single file that installs the
app, a private Python runtime and (optionally) a Windows service, with no
prerequisites on the target machine.

## What the user gets

| | |
|---|---|
| Install location | `%LOCALAPPDATA%\Programs\Customer Success Hub` — **no administrator needed** |
| Data | `%LOCALAPPDATA%\CustomerSuccessHub` (databases, uploads, ticket exports), asked for during setup |
| Python | Bundled. The machine's own Python is neither required nor touched |
| Service | Optional. Registers **Customer Success Hub** in `services.msc`, starts with the machine, restarts on failure — this is the one step that asks for administrator |
| Shortcuts | Start Menu + optional desktop: open the console, run in a console window, jump to the data folder |
| Uninstall | Standard Apps & Features entry. Removes the service and firewall rule; **leaves your data folder alone** |

Because the runtime is private and has pip, the Components panel in the app
still works — spreadsheets, SQL drivers and decks install on demand after
installation, exactly as they do on Linux.

## Building

Needs Windows, [Inno Setup 6.2+](https://jrsoftware.org/isdl.php) and, for the
first build, an internet connection (downloads are cached in `.build\cache`).

```powershell
cd installer
.\build.ps1                                  # base install, ~22 MB
.\build.ps1 -Version 1.1.0 -IncludeOptional  # everything bundled, works fully offline
```

Output lands in `installer\dist\`.

`build.ps1` does the following:

1. Downloads the embeddable CPython (3.12.10 by default) and unpacks it to `payload\runtime`.
2. Rewrites `python312._pth` so `pip` works (`import site`) and the app is importable (`..`), then drops a `.private-runtime` marker the app looks for.
3. Installs pip and `requirements.txt` into that runtime — with `-IncludeOptional`, `requirements-optional.txt` too.
4. Downloads [WinSW](https://github.com/winsw/winsw) as the service wrapper.
5. Stages `app\`, the launchers and the service template.
6. Compiles `CustomerSuccessHub.iss` with ISCC.

Prefer CI? `.github/workflows/windows-installer.yml` does the same on a
`windows-latest` runner — run it from the Actions tab, or push a `v*` tag to
attach the installer to a release.

## Files

| File | What it is |
|---|---|
| `build.ps1` | Stages the payload and compiles the installer |
| `CustomerSuccessHub.iss` | Inno Setup script: wizard, tasks, shortcuts, elevation for the service |
| `bin\open.cmd` | Start Menu shortcut target — starts the server if needed, then opens the browser |
| `bin\run-console.cmd` | Runs the server in a visible console (no service) |
| `bin\service.ps1` | `install` / `uninstall` / `start` / `stop` / `restart` / `status` / `firewall` |
| `service\CustomerSuccessHubService.xml.template` | WinSW definition; `service.ps1` fills in the paths and port |

## Managing it afterwards

```powershell
cd "$env:LOCALAPPDATA\Programs\Customer Success Hub\bin"
powershell -ExecutionPolicy Bypass -File service.ps1 -Action status
powershell -ExecutionPolicy Bypass -File service.ps1 -Action restart   # needs admin
```

Port, host and data folder live in `server.env` inside the data folder, and in
`service\CustomerSuccessHubService.xml` for the service. Edit and restart.

Service logs are next to the wrapper:
`%LOCALAPPDATA%\Programs\Customer Success Hub\service\*.log`.

## Known limits

- **Unsigned.** SmartScreen will warn on first run ("More info → Run anyway"), and
  some managed fleets block unsigned installers outright. Sign
  `installer\dist\*.exe` with your own certificate (`signtool sign /fd SHA256 ...`)
  before handing it round if that matters.
- **x64 only.** ARM64 Windows needs the ARM64 embeddable Python; change the
  download in `build.ps1` and the architecture directives in the `.iss`.
- The service runs as **LocalSystem** and writes to the data folder chosen at
  install time. If you point it somewhere unusual, check the permissions.
