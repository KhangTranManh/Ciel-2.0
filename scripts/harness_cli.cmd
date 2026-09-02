@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "HARNESS_STATE=%TEMP%\ciel_prompt_harness"

if "%~1"=="" goto help
if /i "%~1"=="help" goto help
if /i "%~1"=="audit" goto audit
if /i "%~1"=="targets" goto targets
if /i "%~1"=="propose" goto propose
if /i "%~1"=="list" goto list
if /i "%~1"=="show-report" goto show_report
if /i "%~1"=="show-candidate" goto show_candidate
if /i "%~1"=="full-report" goto full_report
if /i "%~1"=="show-project-report" goto show_project_report
if /i "%~1"=="apply" goto apply

echo Unknown command: %~1
echo.
goto help_error

:audit
set "MIN_COUNT=%~2"
set "SINCE_DAYS=%~3"
if not defined MIN_COUNT set "MIN_COUNT=2"
if not defined SINCE_DAYS set "SINCE_DAYS=14"
python -m scripts.prompt_harness --mode audit --min-count "%MIN_COUNT%" --since-days "%SINCE_DAYS%"
exit /b %ERRORLEVEL%

:targets
python -m scripts.prompt_harness --mode targets
exit /b %ERRORLEVEL%

:propose
if "%~2"=="" (
    echo Missing exact signature.
    echo Example: scripts\harness_cli.cmd propose "SELF_CORRECTION:OTHER / stealth_search"
    exit /b 2
)
set "MIN_COUNT=%~3"
set "SINCE_DAYS=%~4"
if not defined MIN_COUNT set "MIN_COUNT=2"
if not defined SINCE_DAYS set "SINCE_DAYS=14"
python -m scripts.prompt_harness --mode propose --signature "%~2" --min-count "%MIN_COUNT%" --since-days "%SINCE_DAYS%"
exit /b %ERRORLEVEL%

:list
if not exist "%HARNESS_STATE%" (
    echo No harness state directory exists yet: %HARNESS_STATE%
    exit /b 0
)
echo Reports:
dir /b /a-d /o-d "%HARNESS_STATE%\prompt_rewrite_proposals_*.md" 2>nul
echo.
echo Candidates:
dir /b /a-d /o-d "%HARNESS_STATE%\candidate_*.json" 2>nul
exit /b 0

:show_report
set "LATEST="
if exist "%HARNESS_STATE%" for /f "delims=" %%F in ('dir /b /a-d /o-d "%HARNESS_STATE%\prompt_rewrite_proposals_*.md" 2^>nul') do if not defined LATEST set "LATEST=%%F"
if not defined LATEST (
    echo No audit report found. Run: scripts\harness_cli.cmd audit
    exit /b 1
)
echo FILE: %HARNESS_STATE%\%LATEST%
echo.
type "%HARNESS_STATE%\%LATEST%"
exit /b 0

:show_candidate
if not "%~2"=="" goto show_named_candidate
set "LATEST="
if exist "%HARNESS_STATE%" for /f "delims=" %%F in ('dir /b /a-d /o-d "%HARNESS_STATE%\candidate_*.json" 2^>nul') do if not defined LATEST set "LATEST=%%F"
if not defined LATEST (
    echo No candidate found. Run audit, then propose an eligible signature.
    exit /b 1
)
echo NOTE: This is the latest EXISTING candidate; it may predate a blocked/rejected propose run.
echo FILE: %HARNESS_STATE%\%LATEST%
echo.
type "%HARNESS_STATE%\%LATEST%"
exit /b 0

:show_named_candidate
if /i not "%~2"=="%~nx2" (
    echo Only a candidate filename from %HARNESS_STATE% is accepted.
    exit /b 2
)
if not exist "%HARNESS_STATE%\%~2" (
    echo Candidate not found: %HARNESS_STATE%\%~2
    exit /b 2
)
echo FILE: %HARNESS_STATE%\%~2
echo.
type "%HARNESS_STATE%\%~2"
exit /b 0

:full_report
set "UNIT_OPTION="
if /i "%~2"=="quick" set "UNIT_OPTION=--skip-unit"
python -m scripts.prompt_harness --mode project-report --min-count 1 --since-days 0 %UNIT_OPTION%
exit /b %ERRORLEVEL%

:show_project_report
set "LATEST="
for /f "delims=" %%F in ('dir /b /a-d /o-d "agent_output\ciel_harness_project_report_*.md" 2^>nul') do if not defined LATEST set "LATEST=%%F"
if not defined LATEST (
    echo No full project report found. Run: scripts\harness_cli.cmd full-report
    exit /b 1
)
echo FILE: %CD%\agent_output\%LATEST%
echo.
type "agent_output\%LATEST%"
exit /b 0

:apply
if "%~2"=="" (
    echo Missing candidate filename. Use: scripts\harness_cli.cmd list
    exit /b 2
)
if /i not "%~3"=="APPLY" (
    echo Apply requires explicit confirmation.
    echo Example: scripts\harness_cli.cmd apply "candidate_YYYYMMDD_HHMMSS.json" APPLY
    exit /b 2
)
if /i not "%~2"=="%~nx2" (
    echo Only a candidate filename from %HARNESS_STATE% is accepted.
    exit /b 2
)
if not exist "%HARNESS_STATE%\%~2" (
    echo Candidate not found: %HARNESS_STATE%\%~2
    exit /b 2
)
python -m scripts.prompt_harness --mode apply --candidate "%HARNESS_STATE%\%~2" --yes
exit /b %ERRORLEVEL%

:help
echo Ciel prompt harness - Windows CMD
echo.
echo   scripts\harness_cli.cmd audit [min_count] [since_days]
echo   scripts\harness_cli.cmd targets
echo   scripts\harness_cli.cmd propose "EXACT SIGNATURE" [min_count] [since_days]
echo   scripts\harness_cli.cmd list
echo   scripts\harness_cli.cmd show-report
echo   scripts\harness_cli.cmd show-candidate [candidate_FILENAME.json]
echo   scripts\harness_cli.cmd full-report [quick]
echo   scripts\harness_cli.cmd show-project-report
echo   scripts\harness_cli.cmd apply "candidate_FILENAME.json" APPLY
echo.
echo Reports and candidates stay in: %HARNESS_STATE%
echo Apply still validates policy, hashes, prompt contracts, and the full unit suite.
exit /b 0

:help_error
call :help
exit /b 2
