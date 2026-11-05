# Local CAD Environment

## Configure local paths

This skill is portable. Configure machine-specific CAD paths with environment variables instead of hard-coding them in the skill:

```powershell
$env:AUTOCAD_EXE = "C:\Program Files\Autodesk\AutoCAD 2025\acad.exe"
$env:ACCORECONSOLE_EXE = "C:\Program Files\Autodesk\AutoCAD 2025\accoreconsole.exe"
$env:SOLIDWORKS_MCP_HOME = "$env:USERPROFILE\.codex\mcp\SolidworksMCP-python"
```

If your AutoCAD is installed under another drive or product folder, point `AUTOCAD_EXE` and `ACCORECONSOLE_EXE` to those executables.

## MCP status checks

Check the active Codex MCP config before assuming CAD MCP tools are exposed in the session. If no `solidworks`, `freecad`, or `autocad` server is configured, use script/COM fallbacks and report the limitation.

A local SolidWorks MCP project can be started manually when installed:

```powershell
Set-Location -LiteralPath $env:SOLIDWORKS_MCP_HOME
& ".\.venv\Scripts\python.exe" -m solidworks_mcp.server
```

Example Codex MCP config shape:

```toml
[mcp_servers.solidworks]
command = 'C:\Users\<you>\.codex\mcp\SolidworksMCP-python\.venv\Scripts\python.exe'
args = ['-m', 'solidworks_mcp.server']
startup_timeout_sec = 120
```

Expected SolidWorks MCP startup markers:

- `Platform: Windows`
- `SolidWorks COM interface is available`
- `Registered ... SolidWorks tools`
- `Connected to SolidWorks`

## General dependency checks

Use these probes before diagnosing script failures:

```powershell
python -c "import ezdxf; print('ezdxf OK')"
python -c "import reportlab; print('reportlab OK')"
python -c "import fitz; print('PyMuPDF OK')"
```

For SolidWorks COM fallback:

```powershell
& "$env:SOLIDWORKS_MCP_HOME\.venv\Scripts\python.exe" -c "import win32com.client; print('win32com OK')"
```

## Workspace history

Prior generated drawings, render crops, and patch scripts can be useful as examples, but treat them as provenance for one project rather than generic source of truth for new CAD tasks. For new work, start from an explicit JSON spec or CAD source file and regenerate outputs in the active workspace.
