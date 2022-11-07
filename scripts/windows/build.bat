@echo off
MKDIR app dep 2>NUL

SET ERRORLEVEL=
WHERE /Q python
IF ERRORLEVEL 1 (
	ECHO python 3.8.10 and above is required to proceed. Exiting...
	EXIT /B -1
)

SET ERRORLEVEL=
WHERE /Q git
IF ERRORLEVEL 1 (
	ECHO git is required to proceed. Exiting...
	EXIT /B -1
)

SET ERRORLEVEL=
WHERE /Q wget
IF ERRORLEVEL 1 (
	ECHO wget is required to proceed. Exiting...
	EXIT /B -1
)

REM Get Python version
FOR /f "tokens=1-2" %%i in ('python --version') do (
	set PYTHON_VERSION=%%j
	IF /i "%PYTHON_VERSION:~0,1%" == "3" (
		ECHO Python version is valid
	) ELSE (
		ECHO Found python version: %PYTHON_VERSION%
		ECHO python 3.8.10 and above is required to proceed. Exiting...
		EXIT /B -1
	)
)
@echo Installed python version: %PYTHON_VERSION%

SET ERRORLEVEL=
WHERE /Q pip
IF ERRORLEVEL 1 (
        ECHO pip wasn't found. Attempting to install...
        wget -O .\dep\get-pip.py https://bootstrap.pypa.io/get-pip.py
        python  .\dep\get-pip.py
	SET ERRORLEVEL=
	WHERE /Q pip
        IF ERRORLEVEL 1 (
                ECHO Unable to install pip. See errors above. Error: %ERRORLEVEL% Exiting...
                EXIT /B 1
        )
	ECHO Successfully installed pip
)
python -m pip install --upgrade pip
ECHO pip is installed.

IF EXIST .\py-spy\py-spy.exe (
	ECHO Found py-spy.exe
) ELSE (
	ECHO Building py-spy executable...
	SET ERRORLEVEL=
	CALL .\scripts\windows\build-pyspy.bat
	IF ERRORLEVEL 1 (
		ECHO Building py-spy failed. See Errors above.
		EXIT /B 1 
	)
)

git submodule update --init --recursive

CD app

MKDIR granulate-utils 2> NUL
COPY ..\requirements.txt requirements.txt
FOR %%i in ("..\granulate-utils\setup.py" "..\granulate-utils\requirements.txt" "..\granulate-utils\README.md") do COPY "%%i" granulate-utils
ECHO D | XCOPY ..\granulate-utils\granulate_utils .\granulate-utils\granulate_utils /E
python -m pip install --no-cache-dir -r requirements.txt

COPY ..\exe-requirements.txt exe-requirements.txt
python -m pip install --no-cache-dir -r exe-requirements.txt
MKDIR gprofiler\resources\python
COPY ..\py-spy\py-spy.exe gprofiler\resources\python

ECHO D | XCOPY ..\gprofiler .\gprofiler /E
FOR %%i in ("..\pyi_build.py" "..\pyinstaller.spec") do COPY "%%i" .
pyinstaller pyinstaller.spec
