@echo off
setlocal EnableExtensions

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%" || exit /b 1

set "PYTHON=python"
if defined TSO_PYTHON set "PYTHON=%TSO_PYTHON%"

rem Parallel decode workers. Default 0 = automatic/all CPU cores. Set
rem TSO_JOBS=1 for the sequential path. Archives are independent, so output
rem is identical.
set "JOBS=0"
if defined TSO_JOBS set "JOBS=%TSO_JOBS%"

if "%~1"=="" goto usage
if /I "%~1"=="finalize" goto finalize
if "%~2"=="" goto usage
goto single

rem --------------------------------------------------------------- single volume
:single
set "SOURCE_DIR=%~1"
set "VOL_DIR=%~2"
set "VOL_TITLE=%~3"
set "VOL_DATE=%~4"
call :process_content "%SOURCE_DIR%" "%VOL_DIR%" || exit /b 1
"%PYTHON%" tools\build_coverage.py --root . --vol "%VOL_DIR%" --data "%SOURCE_DIR%\data" || exit /b 1
if not "%VOL_TITLE%"=="" (
    "%PYTHON%" -c "import json,sys; json.dump({'title': sys.argv[1], 'release': sys.argv[2]}, open(sys.argv[3], 'w', encoding='utf-8'))" "%VOL_TITLE%" "%VOL_DATE%" "%VOL_DIR%\vol_meta.json" || exit /b 1
)
echo.
echo Volume "%VOL_DIR%" processed. Run more volumes, then: tso_convert.bat finalize
exit /b 0

rem ------------------------------------------------- shared site-build tail
:finalize
"%PYTHON%" tools\recover_v1_names.py || exit /b 1
"%PYTHON%" tools\build_catalog.py --root . || exit /b 1
"%PYTHON%" tools\build_wiring.py || exit /b 1
"%PYTHON%" tools\build_site.py --root . || exit /b 1
"%PYTHON%" tools\rewrite_links.py || exit /b 1
"%PYTHON%" tools\fix_svg.py || exit /b 1
"%PYTHON%" tools\verify_links.py || exit /b 1
echo.
echo Pipeline complete. Open index.html to browse.
exit /b 0

rem ---------------------------------------------------------------- subroutine
:process_content
set "_DISC=%~1"
set "_VOL=%~2"
set "_LOCALE=%_DISC%\content\useni4"
set "_DATA=%_DISC%\data"
if not exist "%_LOCALE%\" (
    echo ERROR: missing content locale directory: "%_LOCALE%"
    exit /b 1
)
if not exist "%_DATA%\" (
    echo ERROR: missing coverage data directory: "%_DATA%"
    exit /b 1
)
if not exist "%_VOL%\" mkdir "%_VOL%" || exit /b 1
"%PYTHON%" tools\inventory.py "%_LOCALE%" --out "%_VOL%\inventory.json" || exit /b 1
"%PYTHON%" tools\extract_all.py "%_LOCALE%" --out "%_VOL%\content" --jobs %JOBS% || exit /b 1
exit /b 0

:usage
echo Usage:
echo   tso_convert.bat ^<source_dir^> ^<vol_name^> ["Display Title" ["Release date"]]
echo   tso_convert.bat finalize
echo.
echo   source_dir  local source root containing content\useni4 and data\
echo   vol_name    output directory name for this volume, e.g. vol_2005_06
echo               (any vol_* name)
echo.
echo Examples:
echo   tso_convert.bat D:\ vol_example_01 "Example Local Archive" "Local build"
echo   tso_convert.bat finalize
echo.
echo Set TSO_PYTHON to override the Python executable, TSO_JOBS to set decode workers.
exit /b 2
