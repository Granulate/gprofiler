IF NOT EXIST .\dep\dotnet-install.ps1 (
    ECHO Downloading dotnet installation script...
    curl -sfLo -O .\dep\dotnet-install.ps1 https://dot.net/v1/dotnet-install.ps1
)

SET DOTNET_VERSION=6.0.405
SET DOTNET_TRACE_VERSION=6.0.257301
SET DOTNET_SHARED_VERSION=6.0.13
powershell -ExecutionPolicy Bypass -File .\dep\dotnet-install.ps1 dotnet -Version %DOTNET_VERSION% -InstallDir .\dep\dotnet
.\dep\dotnet\dotnet.exe tool install --tool-path .\dep\dotnet\tools dotnet-trace --version %DOTNET_TRACE_VERSION%
MKDIR app\gprofiler\resources\dotnet
FOR %%i IN (".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\Microsoft.CSharp.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\Microsoft.NETCore.App.deps.json",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\Microsoft.Win32.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\mscorlib.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\netstandard.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.ObjectModel.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Collections.Concurrent.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Collections.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.ComponentModel.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.ComponentModel.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.ComponentModel.TypeConverter.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Console.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Core.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Data.Common.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Data.DataSetExtensions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Data.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Diagnostics.Process.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Diagnostics.Tracing.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.IO.FileSystem.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.IO.FileSystem.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Linq.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Linq.Expressions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Memory.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Private.CoreLib.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Private.Uri.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Runtime.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Runtime.Extensions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Runtime.InteropServices.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Runtime.InteropServices.RuntimeInformation.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Text.RegularExpressions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Threading.Channels.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Threading.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Threading.Overlapped.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Threading.Tasks.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Threading.Thread.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Threading.ThreadPool.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.IO.Pipes.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Net.Sockets.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Net.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Security.Principal.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.IO.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Security.Cryptography.Algorithms.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Security.Cryptography.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\libSystem.Security.Cryptography.Native.OpenSsl.so"
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Runtime.CompilerServices.Unsafe.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Diagnostics.TraceSource.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Reflection.Emit.ILGeneration.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Reflection.Emit.Lightweight.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\%DOTNET_SHARED_VERSION%\System.Reflection.Primitives.dll",
            ) DO COPY %%i .\app\gprofiler\resources\dotnet\shared
ECHO D | XCOPY .\dep\dotnet\tools .\app\gprofiler\resources\dotnet\tools /E
COPY .\dep\dotnet\host\fxr\%DOTNET_SHARED_VERSION%\hostfxr.dll .\app\gprofiler\resources\dotnet\tools\.store\dotnet-trace\%DOTNET_VERSION%\dotnet-trace\%DOTNET_VERSION%\tools\netcoreapp3.1\any\
@REM  rmdir /s /q .\dep\dotnet
.\app\gprofiler\resources\dotnet\tools\dotnet-trace.exe -h
