# NOTE: Make sure to import any new process profilers to load it
from gprofiler.platform import is_linux
from gprofiler.profilers.python import PythonProfiler

if is_linux():
    from gprofiler.profilers.dotnet import DotnetProfiler
    from gprofiler.profilers.java import JavaProfiler
    from gprofiler.profilers.perf import SystemProfiler
    from gprofiler.profilers.php import PHPSpyProfiler
    from gprofiler.profilers.ruby import RbSpyProfiler

__all__ = ["PythonProfiler"]

if is_linux():
    __all__ += ["JavaProfiler", "PHPSpyProfiler", "RbSpyProfiler", "SystemProfiler", "DotnetProfiler"]

del is_linux
