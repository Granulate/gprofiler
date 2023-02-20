IF NOT EXIST .\dep\dotnet-install.ps1 (
    ECHO Downloading dotnet installation script...
    wget -O .\dep\dotnet-install.ps1 https://dot.net/v1/dotnet-install.ps1
)

SET DOTNET_VERSION=6.0.405
powershell -ExecutionPolicy Bypass -File .\dep\dotnet-install.ps1 dotnet -Version %DOTNET_VERSION% -InstallDir .\dep\dotnet
.\dep\dotnet\dotnet.exe tool install --tool-path .\dep\dotnet\tools dotnet-trace --version "(6.*,7.0)"
MKDIR app\gprofiler\resources\dotnet
FOR %%i IN (".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\Microsoft.CSharp.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\Microsoft.NETCore.App.deps.json",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\Microsoft.Win32.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\mscorlib.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\netstandard.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.ObjectModel.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Collections.Concurrent.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Collections.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.ComponentModel.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.ComponentModel.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.ComponentModel.TypeConverter.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Console.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Core.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Data.Common.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Data.DataSetExtensions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Data.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Diagnostics.Process.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Diagnostics.Tracing.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.IO.FileSystem.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.IO.FileSystem.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Linq.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Linq.Expressions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Memory.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Private.CoreLib.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Private.Uri.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Runtime.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Runtime.Extensions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Runtime.InteropServices.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Runtime.InteropServices.RuntimeInformation.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Text.RegularExpressions.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Threading.Channels.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Threading.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Threading.Overlapped.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Threading.Tasks.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Threading.Thread.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Threading.ThreadPool.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.IO.Pipes.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Net.Sockets.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Net.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Security.Principal.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.IO.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Security.Cryptography.Algorithms.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Security.Cryptography.Primitives.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\libSystem.Security.Cryptography.Native.OpenSsl.so"
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Runtime.CompilerServices.Unsafe.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Diagnostics.TraceSource.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Reflection.Emit.ILGeneration.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Reflection.Emit.Lightweight.dll",
            ".\dep\dotnet\shared\Microsoft.NETCore.App\6.0.0\System.Reflection.Primitives.dll",
            ) DO COPY %%i .\app\gprofiler\resources\dotnet\shared
ECHO D | XCOPY .\dep\dotnet\tools .\app\gprofiler\resources\dotnet\tools /E
COPY .\dep\dotnet\host\fxr\6.0.0\hostfxr.dll .\app\gprofiler\resources\dotnet\tools\.store\dotnet-trace\6.0.351802\dotnet-trace\6.0.351802\tools\netcoreapp3.1\any\
rmdir /s /q .\dep\dotnet
