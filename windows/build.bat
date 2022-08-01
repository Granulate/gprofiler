@echo off
MKDIR app src py-spy 2>NUL
REM Download Build Tools for Visual Studio
REM https://aka.ms/vs/17/release/vs_BuildTools.exe
WHERE python
IF %ERRORLEVEL% NEQ 0 (
	ECHO python wasn't found. Attempting to install...
	ECHO Shell will close once complete. Re-run build.bat to proceed
	.\dep\python-3.9.13-amd64.exe /passive PrependPath=1 Include_test=0
)
REM Get Python version
FOR /f "tokens=1-2" %%i in ('python --version') do (
	set PYTHON_VERSION=%%j
	IF /i "%PYTHON_VERSION:~0,1%" == "3" (
		ECHO Python version is valid
	) ELSE (
		ECHO Python 3 is required. Attempying to install...
		ECHO Shell will close once complete. Re-run build.bat to proceed
		.\deb\python-3.9.13-amd64.exe /passive PrependPath=1 Include_test=0
	)
)
@echo Installed python version: %PYTHON_VERSION%
CALL build-pyspy.bat
CD app
Rem On VPN: pip install --proxy "http://proxy-dmz.intel.com:912" --no-cache-dir --user --upgrade pip

pip install --no-cache-dir --user --upgrade pip
MKDIR granulate-utils 2> NUL
COPY ..\src\gprofiler\requirements.txt requirements.txt
FOR %%i in ("..\src\gprofiler\granulate-utils\setup.py" "..\src\gprofiler\granulate-utils\requirements.txt" "..\src\gprofiler\granulate-utils\README.md") do COPY "%%i" granulate-utils
ECHO D | XCOPY ..\src\gprofiler\granulate-utils\granulate_utils .\granulate-utils\granulate_utils /E
python -m pip install --no-cache-dir -r requirements.txt

COPY ..\src\gprofiler\exe-requirements.txt exe-requirements.txt
python -m pip install --no-cache-dir -r exe-requirements.txt
MKDIR gprofiler\resources\python
COPY ..\py-spy\py-spy.exe gprofiler\resources\python

ECHO D | XCOPY ..\src\gprofiler\gprofiler .\gprofiler /E
FOR %%i in ("..\src\gprofiler\pyi_build.py" "..\src\gprofiler\pyinstaller.spec") do COPY "%%i" .
pyinstaller pyinstaller.spec
