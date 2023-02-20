@ECHO OFF
SETLOCAL

SET WORKDIR=%~dp0
SET GOLANG_VERSION=1.16.15
SET GOLANG_DIST_FILE=go%GOLANG_VERSION%.windows-amd64.zip
SET GOLANG_DIST_SITE=https://go.dev/dl
SET DEP_DIR=%WORKDIR%\..\..\dep
SET GOLANG_TARGET_DIR=%DEP_DIR%\go

IF EXIST burn RMDIR /S /Q burn
FOR /f "usebackq tokens=2" %%a IN (`findstr "VERSION" %WORKDIR%\..\burn_version.txt`) DO SET BURN_VERSION=%%a
FOR /f "usebackq tokens=2" %%a IN (`findstr "COMMIT" %WORKDIR%\..\burn_version.txt`) DO SET BURN_COMMIT=%%a
ECHO Burn version: %BURN_VERSION% burn commit: %BURN_COMMIT%

git clone --depth 1 -b %BURN_VERSION% https://github.com/Granulate/burn.git && git -C burn reset --hard %BURN_COMMIT%
IF ERRORLEVEL 1 ( ECHO Getting burn tool failed. & GOTO exit_with_error )

WHERE /Q go || (
    CALL :go_install
    IF ERRORLEVEL 1 ( ECHO Golang installation failed & GOTO exit_with_error )
)

ECHO Building burn.
CD burn
SET CGO_ENABLED=0
go build
IF ERRORLEVEL 1 ( ECHO Burn build failed. & GOTO exit_with_error )
ECHO Burn build complete

GOTO end


@REM
@REM go_install
@REM
@REM download and extract a Golang distribution
:go_install
ECHO Preparing to download and install Golang (locally).
IF EXIST %GOLANG_DIST_FILE% GOTO _go_extract
ECHO Downloading Golang distribution
CALL :download_file %GOLANG_DIST_SITE%/%GOLANG_DIST_FILE% %GOLANG_DIST_FILE%
IF ERRORLEVEL 1 ( ECHO Couldn't download Golang distribution & EXIT /B 1 )

:_go_extract
ECHO Extracting Golang distribution
RMDIR /S /Q %GOLANG_TARGET_DIR% 2>NUL
CALL :extract_file %GOLANG_DIST_FILE% %GOLANG_TARGET_DIR%
IF ERRORLEVEL 1 ( ECHO Couldn't extract Golang distribution & EXIT /B 1 )

SET PATH=%GOLANG_TARGET_DIR%\go\bin;%PATH%
WHERE /Q go
IF ERRORLEVEL 1 ( ECHO Golang installation failed. & EXIT /B 1 )
go version
ECHO Golang installation complete.

EXIT /B 0


@REM
@REM download_file URL FILENAME
@REM
:download_file
SET url=%1
SET filename=%2
WHERE /Q curl && ( SET download_command=curl -sLo %filename% %url% & GOTO _run_download )
WHERE /Q wget && ( SET download_command=wget --no-verbose -O %filename% %url% & GOTO _run_download )
ECHO Missing download commands: either curl or wget is needed.
EXIT /B 1

:_run_download
%download_command%
IF ERRORLEVEL 1 ( ECHO Download failed. & EXIT /B 1 )
ECHO Downloaded %filename%.

EXIT /B 0


@REM
@REM extract_file FILENAME TARGET_DIRECTORY
@REM
:extract_file
SET filename=%1
SET target_directory=%2
WHERE /Q tar && ( SET extract_command=tar xf %filename% -C%target_directory% & GOTO _run_extract )
WHERE /Q 7za && ( SET extract_command=7za x -o%target_directory% -bd -bso0 -bsp0 %filename% & GOTO _run_extract )
ECHO Missing extraction tools: either tar or 7za is needed.
EXIT /B 1

:_run_extract
MKDIR %target_directory% 2>NUL
%extract_command%
IF ERRORLEVEL 1 ( ECHO Extraction failed. & EXIT /B 1 )

EXIT /B 0


:end
ENDLOCAL
EXIT /B 0


:exit_with_error
ENDLOCAL
EXIT /B 1
