IF NOT EXIST .\dep\dotnet-install.ps1 (
    ECHO Downloading dotnet installation script...
    curl -sfLo .\dep\dotnet-install.ps1 https://dot.net/v1/dotnet-install.ps1
    IF ERRORLEVEL 1 ( ECHO Download failed. & EXIT /B 1 )
)

SET DOTNET_VERSION=6.0.405
SET DOTNET_TRACE_VERSION=6.0.351802
SET DOTNET_SHARED_VERSION=6.0.13
powershell -ExecutionPolicy Bypass -File .\dep\dotnet-install.ps1 dotnet -Version %DOTNET_VERSION% -InstallDir .\dep\dotnet
IF ERRORLEVEL 1 ( ECHO dotnet installation failed. & EXIT /B 1 )
.\dep\dotnet\dotnet.exe tool install --tool-path .\dep\dotnet\tools dotnet-trace --version %DOTNET_TRACE_VERSION%
IF ERRORLEVEL 1 ( ECHO dotnet-trace installation failed. & EXIT /B 1 )
MKDIR app\gprofiler\resources\dotnet
for /f "delims=" %%i in (scripts/dotnet_trace_dependencies.txt) do COPY ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\%%i" .\app\gprofiler\resources\dotnet\shared
ECHO D | XCOPY .\dep\dotnet\tools .\app\gprofiler\resources\dotnet\tools /E
COPY .\dep\dotnet\host\fxr\%DOTNET_SHARED_VERSION%\hostfxr.dll .\app\gprofiler\resources\dotnet\tools\.store\dotnet-trace\%DOTNET_TRACE_VERSION%\dotnet-trace\%DOTNET_TRACE_VERSION%\tools\netcoreapp3.1\any\
rmdir /s /q .\dep\dotnet
EXIT /B 0
