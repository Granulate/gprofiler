IF NOT EXIST .\dep\dotnet-install.ps1 (
    ECHO Downloading dotnet installation script...
    wget -O .\dep\dotnet-install.ps1 https://dot.net/v1/dotnet-install.ps1
)

powershell -ExecutionPolicy Bypass -File .\dep\dotnet-install.ps1 -Runtime dotnet -Version 6.0.0 -InstallDir .\dep\dotnet
.\dep\dotnet\dotnet.exe tool install --tool-path .\dep\dotnet\tools dotnet-trace
MKDIR app\gprofiler\resources\dotnet
ECHO D | XCOPY .\dep\dotnet\shared .\app\gprofiler\resources\dotnet\shared /E
ECHO D | XCOPY .\dep\dotnet\tools .\app\gprofiler\resources\dotnet\tools /E

