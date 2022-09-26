# NOTE: Make sure to import any new process profilers to load it
from gprofiler.platform import is_windows
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.python import PythonProfiler

if not is_windows():
    from gprofiler.profilers.dotnet import DotnetProfiler
    from gprofiler.profilers.java import JavaProfiler
    from gprofiler.profilers.php import PHPSpyProfiler
    from gprofiler.profilers.ruby import RbSpyProfiler

__all__ = ["PythonProfiler", "SystemProfiler"]

if not is_windows():
    __all__ += ["JavaProfiler", "PHPSpyProfiler", "RbSpyProfiler", "DotnetProfiler"]

del is_windows
