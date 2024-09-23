#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import functools
import json
import os
from pathlib import Path
from threading import Event
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from granulate_utils.java import (
    CONTAINER_INFO_REGEX,
    DETECTED_JAVA_PROCESSES_REGEX,
    NATIVE_FRAMES_REGEX,
    SIGINFO_REGEX,
    VM_INFO_REGEX,
    JvmFlag,
    JvmVersion,
    is_java_fatal_signal,
    java_exit_code_to_signo,
    parse_jvm_flags,
    parse_jvm_version,
)

from granulate_utils.gprofiler.platform import is_linux
from gprofiler.utils.collapsed_format import parse_one_collapsed

if is_linux():
    from granulate_utils.linux import proc_events
    from granulate_utils.linux.kernel_messages import KernelMessage
    from granulate_utils.linux.oom import get_oom_entry
    from granulate_utils.linux.process import (
        get_mapped_dso_elf_id,
        is_process_running,
        process_exe,
    )
    from granulate_utils.linux.signals import get_signal_entry

from packaging.version import Version
from psutil import Process

from gprofiler.diagnostics import is_diagnostics
from gprofiler.gprofiler_types import (
    ProcessToProfileData,
    ProfileData,
    StackToSampleCount,
    comma_separated_enum_list,
    integer_range,
    positive_integer,
)
from gprofiler.kernel_messages import get_kernel_messages_provider
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.profiler_base import SpawningProcessProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import (
    GPROFILER_DIRECTORY_NAME,
    TEMPORARY_STORAGE_PATH,
    pgrep_maps,
    resource_path,
    wait_event,
)
from gprofiler.utils.perf import can_i_use_perf_events
from gprofiler.utils.process import process_comm, search_proc_maps
from granulate_utils.gprofiler.java import *

logger = get_logger_adapter(__name__)

# directories we check for rw,exec as candidates for libasyncProfiler.so placement.
POSSIBLE_AP_DIRS = (
    TEMPORARY_STORAGE_PATH,
    f"/run/{GPROFILER_DIRECTORY_NAME}",
    f"/opt/{GPROFILER_DIRECTORY_NAME}",
    f"/dev/{GPROFILER_DIRECTORY_NAME}",  # unfortunately, we encoundered some systems that left us no other option
)

class JavaMetadata(ApplicationMetadata):
    def __init__(
        self,
        stop_event: Event,
        jattach_jcmd_runner: JattachJcmdRunner,
        java_collect_jvm_flags: Union[JavaFlagCollectionOptions, List[str]],
    ):
        super().__init__(stop_event)
        self.jattach_jcmd_runner = jattach_jcmd_runner
        self.java_collect_jvm_flags = java_collect_jvm_flags

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        version = get_java_version(process, self._stop_event, logger) or "/java not found"
        # libjvm elfid - we care only about libjvm, not about the java exe itself which is a just small program
        # that loads other libs.
        libjvm_elfid = get_mapped_dso_elf_id(process, "/libjvm")

        jvm_flags: Union[str, List[Dict]]

        try:
            jvm_flags = self.get_jvm_flags_serialized(process)
        except Exception as e:
            logger.exception("Failed to collect JVM flags", pid=process.pid)
            jvm_flags = f"error {type(e).__name__}"

        metadata = {
            "java_version": version,
            "libjvm_elfid": libjvm_elfid,
            "jvm_flags": jvm_flags,
        }

        metadata.update(super().make_application_metadata(process))
        return metadata

    def get_jvm_flags_serialized(self, process: Process) -> List[Dict]:
        return [flag.to_dict() for flag in sorted(self.get_jvm_flags(process), key=lambda flag: flag.name)]

    def get_jvm_flags(self, process: Process) -> Iterable[JvmFlag]:
        if self.java_collect_jvm_flags == JavaFlagCollectionOptions.NONE:
            return []

        filtered_jvm_flags = self.get_supported_jvm_flags(process)

        if self.java_collect_jvm_flags == JavaFlagCollectionOptions.ALL:
            return filtered_jvm_flags
        elif self.java_collect_jvm_flags == JavaFlagCollectionOptions.DEFAULT:
            return filter(self.default_collection_filter_jvm_flag, filtered_jvm_flags)
        else:
            assert isinstance(self.java_collect_jvm_flags, list), f"Unrecognized value: {self.java_collect_jvm_flags}"
            found_flags = [flag for flag in filtered_jvm_flags if flag.name in self.java_collect_jvm_flags]
            missing_flags = set(self.java_collect_jvm_flags) - {flag.name for flag in found_flags}

            # log if the set is not empty
            if missing_flags:
                logger.warning("Missing requested flags:", missing_flags=missing_flags)

            return found_flags

    @staticmethod
    def filter_jvm_flag(flag: JvmFlag) -> bool:
        """
        Filter out flags that are:
        1. Flags that are of ccstr or ccstrlist type (type=ccstr, type=ccstrlist) - we have problems parsing them correctly # noqa: E501
        2. Flags that are manageable (kind=manageable) - they might change during execution
        """
        if flag.type in ["ccstr", "ccstrlist"]:
            return False

        if "manageable" in flag.kind:
            return False

        return True

    @staticmethod
    def default_collection_filter_jvm_flag(flag: JvmFlag) -> bool:
        """
        Filter out flags that are:
        1. Default flags (origin=default), that are constant for the JDK version
        2. Flags that are non-production (kind=notproduct), platform dependent (kind=pd), or in development (kind=develop) # noqa: E501
        """
        if flag.origin == "default":
            return False

        if set(flag.kind).intersection({"notproduct", "pd", "develop"}):
            return False

        return True

    @functools.lru_cache(maxsize=1024)
    def get_supported_jvm_flags(self, process: Process) -> Iterable[JvmFlag]:
        return filter(self.filter_jvm_flag, parse_jvm_flags(self.jattach_jcmd_runner.run(process, "VM.flags -all")))


@functools.lru_cache(maxsize=1)
def asprof_path() -> str:
    return resource_path("java/asprof")


@functools.lru_cache(maxsize=1)
def fdtransfer_path() -> str:
    return resource_path("java/fdtransfer")


@functools.lru_cache(maxsize=1)
def get_ap_version() -> str:
    return Path(resource_path("java/async-profiler-version")).read_text()

@register_profiler(
    "Java",
    possible_modes=["ap", "disabled"],
    default_mode="ap",
    supported_archs=["x86_64", "aarch64"],
    profiler_arguments=[
        ProfilerArgument(
            "--java-no-version-check",
            dest="java_version_check",
            action="store_false",
            help="Skip the JDK version check (that is done before invoking async-profiler). See"
            " _is_jvm_profiling_supported() docs for more information.",
        ),
        ProfilerArgument(
            "--java-async-profiler-mode",
            dest="java_async_profiler_mode",
            choices=SUPPORTED_AP_MODES + ["auto"],
            default="auto",
            help="Select async-profiler's mode: 'cpu' (based on perf_events & fdtransfer), 'itimer' (no perf_events)"
            " or 'auto' (select 'cpu' if perf_events are available; otherwise 'itimer'). Defaults to '%(default)s'.",
        ),
        ProfilerArgument(
            "--java-async-profiler-safemode",
            dest="java_async_profiler_safemode",
            default=JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
            type=integer_range(0, 0x40),
            metavar="[0-63]",
            help="Controls the 'safemode' parameter passed to async-profiler. This is parameter denotes multiple"
            " bits that describe different stack recovery techniques which async-profiler uses. In a future release,"
            " these optinos will be migrated to the 'features' parameter."
            " Defaults to '%(default)s'.",
        ),
        ProfilerArgument(
            "--java-async-profiler-features",
            dest="java_async_profiler_features",
            default=DEFAULT_AP_FEATURES,
            metavar=",".join(SUPPORTED_AP_FEATURES),
            type=functools.partial(comma_separated_enum_list, SUPPORTED_AP_FEATURES),
            help="Controls the 'features' parameter passed to async-profiler. This is parameter is a comma-separated"
            " list of options which describe async-profiler's available features (see StackWalkFeatures"
            " enum in async-profiler's code, in arguments.h)."
            " Defaults to '%(default)s').",
        ),
        ProfilerArgument(
            "--java-async-profiler-args",
            dest="java_async_profiler_args",
            type=str,
            help="Additional arguments to pass directly to async-profiler (start & stop commands)",
        ),
        ProfilerArgument(
            "--java-safemode",
            dest="java_safemode",
            type=str,
            const=JAVA_SAFEMODE_ALL,
            nargs="?",
            default=",".join(JAVA_SAFEMODE_DEFAULT_OPTIONS),
            help="Sets the Java profiler safemode options. Default is: %(default)s.",
        ),
        ProfilerArgument(
            "--java-jattach-timeout",
            dest="java_jattach_timeout",
            type=positive_integer,
            default=AsyncProfiledProcess._DEFAULT_JATTACH_TIMEOUT,
            help="Timeout for jattach operations (start/stop AP, etc)",
        ),
        ProfilerArgument(
            "--java-async-profiler-mcache",
            dest="java_async_profiler_mcache",
            # this is "unsigned char" in AP's code
            type=integer_range(0, 256),
            metavar="[0-255]",
            default=AsyncProfiledProcess._DEFAULT_MCACHE,
            help="async-profiler mcache option (defaults to %(default)s)",
        ),
        ProfilerArgument(
            "--java-collect-spark-app-name-as-appid",
            dest="java_collect_spark_app_name_as_appid",
            action="store_true",
            default=False,
            help="In case of Spark application executor process - add the name of the Spark application to the appid.",
        ),
        ProfilerArgument(
            "--java-async-profiler-no-report-meminfo",
            dest="java_async_profiler_report_meminfo",
            action="store_false",
            default=True,
            help="Disable collection of async-profiler meminfo at the end of each cycle (collected by default)",
        ),
        ProfilerArgument(
            "--java-collect-jvm-flags",
            dest="java_collect_jvm_flags",
            type=str,
            nargs="?",
            default=JavaFlagCollectionOptions.DEFAULT.value,
            help="Comma-separated list of JVM flags to collect from the JVM process, 'all' to collect all flags,"
            "'default' for default flag filtering settings; see default_collection_filter_jvm_flag(), or 'none' to "
            "disable collection of JVM flags. Defaults to '%(default)s'",
        ),
        ProfilerArgument(
            "--java-full-hserr",
            dest="java_full_hserr",
            action="store_true",
            default=False,
            help="Log the full hs_err instead of excerpts only, if one is found for a profiled Java application",
        ),
        ProfilerArgument(
            "--java-include-method-modifiers",
            dest="java_include_method_modifiers",
            action="store_true",
            default=False,
            help="Add method modifiers to profiling data",
        ),
        ProfilerArgument(
            "--java-line-numbers",
            dest="java_line_numbers",
            choices=["none", "line-of-function"],
            default="none",
            help="Select if async-profiler should add line numbers to frames",
        ),
    ],
    supported_profiling_modes=["cpu", "allocation"],
)
class JavaProfiler(SpawningProcessProfilerBase):
    JDK_EXCLUSIONS: List[str] = []  # currently empty
    # Major -> (min version, min build number of version)
    MINIMAL_SUPPORTED_VERSIONS = {
        7: (Version("7.76"), 4),
        8: (Version("8.25"), 17),
        11: (Version("11.0.2"), 7),
        12: (Version("12.0.1"), 12),
        13: (Version("13.0.1"), 9),
        14: (Version("14"), 33),
        15: (Version("15.0.1"), 9),
        16: (Version("16"), 36),
        17: (Version("17"), 11),
        18: (Version("18"), 36),
        19: (Version("19.0.1"), 10),
        21: (Version("21"), 22),
    }

    # extra timeout seconds to add to the duration itself.
    # once the timeout triggers, AP remains stopped, so if it triggers before we tried to stop
    # AP ourselves, we'll be in messed up state. hence, we add 30s which is enough.
    _AP_EXTRA_TIMEOUT_S = 30

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        java_version_check: bool,
        java_async_profiler_mode: str,
        java_async_profiler_safemode: int,
        java_async_profiler_features: List[str],
        java_async_profiler_args: str,
        java_safemode: str,
        java_jattach_timeout: int,
        java_async_profiler_mcache: int,
        java_collect_spark_app_name_as_appid: bool,
        java_mode: str,
        java_async_profiler_report_meminfo: bool,
        java_collect_jvm_flags: str,
        java_full_hserr: bool,
        java_include_method_modifiers: bool,
        java_line_numbers: str,
    ):
        assert java_mode == "ap", "Java profiler should not be initialized, wrong java_mode value given"
        super().__init__(frequency, duration, profiler_state)
        # Alloc interval is passed in frequency in allocation profiling (in bytes, as async-profiler expects)
        self._interval = (
            frequency_to_ap_interval(frequency) if self._profiler_state.profiling_mode == "cpu" else frequency
        )
        # simple version check, and
        self._simple_version_check = java_version_check
        if not self._simple_version_check:
            logger.warning("Java version checks are disabled")
        self._init_ap_mode(self._profiler_state.profiling_mode, java_async_profiler_mode)
        self._ap_safemode = java_async_profiler_safemode
        self._ap_features = java_async_profiler_features
        self._ap_args = java_async_profiler_args
        self._jattach_timeout = java_jattach_timeout
        self._ap_mcache = java_async_profiler_mcache
        self._collect_spark_app_name = java_collect_spark_app_name_as_appid
        self._init_java_safemode(java_safemode)
        self._should_profile = True
        # if set, profiling is disabled due to this safemode reason.
        self._safemode_disable_reason: Optional[str] = None
        self._want_to_profile_pids: Set[int] = set()
        self._profiled_pids: Set[int] = set()
        self._pids_to_remove: Set[int] = set()
        self._pid_to_java_version: Dict[int, Optional[str]] = {}
        self._kernel_messages_provider = get_kernel_messages_provider()
        self._enabled_proc_events_java = False
        self._collect_jvm_flags = self._init_collect_jvm_flags(java_collect_jvm_flags)
        self._jattach_jcmd_runner = JattachJcmdRunner(
            stop_event=self._profiler_state.stop_event,
            jattach_timeout=self._jattach_timeout,
            logger=logger,
        )
        self._ap_timeout = self._duration + self._AP_EXTRA_TIMEOUT_S
        application_identifiers.ApplicationIdentifiers.init_java(self._jattach_jcmd_runner)
        self._metadata = JavaMetadata(
            self._profiler_state.stop_event, self._jattach_jcmd_runner, self._collect_jvm_flags
        )
        self._report_meminfo = java_async_profiler_report_meminfo
        self._java_full_hserr = java_full_hserr
        self._include_method_modifiers = java_include_method_modifiers
        self._java_line_numbers = java_line_numbers

    def _init_ap_mode(self, profiling_mode: str, ap_mode: str) -> None:
        assert profiling_mode in ("cpu", "allocation"), "async-profiler support only cpu/allocation profiling modes"
        if profiling_mode == "allocation":
            ap_mode = "alloc"

        elif ap_mode == "auto":
            ap_mode = "cpu" if can_i_use_perf_events() else "itimer"
            logger.debug("Auto selected AP mode", ap_mode=ap_mode)

        assert ap_mode in SUPPORTED_AP_MODES, f"unexpected ap mode: {ap_mode}"
        self._mode = ap_mode

    def _init_java_safemode(self, java_safemode: str) -> None:
        if java_safemode == JAVA_SAFEMODE_ALL:
            self._java_safemode = JAVA_SAFEMODE_ALL_OPTIONS
        else:
            # accept "" as empty, because sometimes people confuse and use --java-safemode="" in non-shell
            # environment (e.g DaemonSet args) and thus the "" isn't eaten by the shell.
            self._java_safemode = java_safemode.split(",") if (java_safemode and java_safemode != '""') else []

        assert all(
            o in JAVA_SAFEMODE_ALL_OPTIONS for o in self._java_safemode
        ), f"unknown options given in Java safemode: {self._java_safemode!r}"

        if self._java_safemode:
            logger.debug("Java safemode enabled", safemode=self._java_safemode)

        if JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS in self._java_safemode:
            assert self._simple_version_check, (
                "Java version checks are mandatory in"
                f" --java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS}"
            )

    def _init_collect_jvm_flags(self, java_collect_jvm_flags: str) -> Union[JavaFlagCollectionOptions, List[str]]:
        # accept "" as empty, because sometimes people confuse and use --java-collect-jvm-flags="" in non-shell
        # environment (e.g DaemonSet args) and thus the "" isn't eaten by the shell.
        if java_collect_jvm_flags == "":
            return JavaFlagCollectionOptions.NONE
        if java_collect_jvm_flags in (
            java_flag_collection_option.value for java_flag_collection_option in JavaFlagCollectionOptions
        ):
            return JavaFlagCollectionOptions(java_collect_jvm_flags)
        else:
            # Handle spaces between input flag list
            return [collect_jvm_flag.strip() for collect_jvm_flag in java_collect_jvm_flags.split(",")]

    def _disable_profiling(self, cause: str) -> None:
        if self._safemode_disable_reason is None and cause in self._java_safemode:
            logger.warning("Java profiling has been disabled, will avoid profiling any new java processes", cause=cause)
            self._safemode_disable_reason = cause

    def _profiling_skipped_profile(self, reason: str, comm: str) -> ProfileData:
        return ProfileData(self._profiling_error_stack("skipped", reason, comm), None, None, None)

    def _is_jvm_type_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

    def _is_zing_vm_supported(self, jvm_version: JvmVersion) -> bool:
        # Zing >= 18 is assumed to support AsyncGetCallTrace per
        # https://github.com/jvm-profiling-tools/async-profiler/issues/153#issuecomment-452038960
        assert jvm_version.zing_version is not None  # it's Zing so should be non-None.

        # until proven otherwise, we assume ZVM-17168 is affecting 18, 19.
        if jvm_version.zing_version.major < 20:
            return False

        # try to filter versions exhibiting ZVM-17168, from Zing release notes https://docs.azul.com/prime/release-notes
        # it seems that the Zing 20 product line has it, so we filter it out here.
        if jvm_version.zing_version.major == 20:
            if jvm_version.zing_version.minor >= 10:
                # Fixed at 20.10.0.0 - https://docs.azul.com/prime/release-notes#prime_stream_20_10_0_0
                return True
            if jvm_version.zing_version.minor == 8:
                # Fixed at 20.08.101.0 - https://docs.azul.com/prime/release-notes#prime_stable_20_08_101_0
                return jvm_version.zing_version.micro >= 101

            # others are faulty with ZVM-17168.
            return False

        # others are considered okay.
        return True

    def _check_jvm_supported_extended(self, jvm_version: JvmVersion) -> bool:
        if jvm_version.version.major not in self.MINIMAL_SUPPORTED_VERSIONS:
            logger.warning("Unsupported JVM version", jvm_version=repr(jvm_version))
            return False
        min_version, min_build = self.MINIMAL_SUPPORTED_VERSIONS[jvm_version.version.major]
        if jvm_version.version < min_version:
            logger.warning("Unsupported JVM version", jvm_version=repr(jvm_version))
            return False
        elif jvm_version.version == min_version:
            if jvm_version.build < min_build:
                logger.warning("Unsupported JVM version", jvm_version=repr(jvm_version))
                return False

        return True

    def _check_jvm_supported_simple(self, process: Process, java_version_output: str, jvm_version: JvmVersion) -> bool:
        if not self._is_jvm_type_supported(java_version_output):
            logger.warning("Unsupported JVM type", java_version_output=java_version_output)
            return False

        # Zing checks
        if jvm_version.vm_type == "Zing":
            if not self._is_zing_vm_supported(jvm_version):
                logger.warning("Unsupported Zing VM version", jvm_version=repr(jvm_version))
                return False

        # HS checks
        if jvm_version.vm_type == "HotSpot":
            if not jvm_version.version.major > 6:
                logger.warning("Unsupported HotSpot version", jvm_version=repr(jvm_version))
                return False

        return True

    def _is_jvm_profiling_supported(self, process: Process, exe: str, java_version_output: Optional[str]) -> bool:
        """
        This is the core "version check" function.
        We have 3 modes of operation:
        1. No checks at all - java-extended-version-checks is NOT present in --java-safemode, *and*
           --java-no-version-check is passed. In this mode we'll profile all JVMs.
        2. Default - neither java-extended-version-checks nor --java-no-version-check are passed,
           this mode is called "simple checks" and we run minimal checks - if profiled process is
           basenamed "java", we get the JVM version and make sure that for Zing, we attempt profiling
           only if Zing version is >18, and for HS, only if JDK>6. If process is not basenamed "java" we
           just profile it.
        3. Extended - java-extended-version-checks is passed, we only profile processes which are basenamed "java",
           who pass the criteria enforced by the default mode ("simple checks") and additionally all checks
           performed by _check_jvm_supported_extended().
        """
        if JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS in self._java_safemode:
            if java_version_output is None:  # we don't get the java version if we cannot find matching java binary
                logger.warning(
                    "Couldn't get Java version for non-java basenamed process, skipping... (disable "
                    f"--java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS} to profile it anyway)",
                    pid=process.pid,
                    exe=exe,
                )
                return False

            jvm_version = parse_jvm_version(java_version_output)
            if not self._check_jvm_supported_simple(process, java_version_output, jvm_version):
                return False

            if not self._check_jvm_supported_extended(jvm_version):
                logger.warning(
                    "Process running unsupported Java version, skipping..."
                    f" (disable --java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS}"
                    " to profile it anyway)",
                    pid=process.pid,
                    java_version_output=java_version_output,
                )
                return False
        else:
            if self._simple_version_check and java_version_output is not None:
                jvm_version = parse_jvm_version(java_version_output)
                if not self._check_jvm_supported_simple(process, java_version_output, jvm_version):
                    return False

        return True

    def _check_async_profiler_loaded(self, process: Process) -> bool:
        if JavaSafemodeOptions.AP_LOADED_CHECK not in self._java_safemode:
            # don't care
            return False

        for mmap in process.memory_maps():
            # checking only with GPROFILER_DIRECTORY_NAME and not TEMPORARY_STORAGE_PATH;
            # the resolved path of TEMPORARY_STORAGE_PATH might be different from TEMPORARY_STORAGE_PATH itself,
            # and in the mmap.path we're seeing the resolved path. it's a hassle to resolve it here - this
            # check is good enough, possibly only too strict, not too loose.
            if "libasyncProfiler.so" in mmap.path and f"/{GPROFILER_DIRECTORY_NAME}/" not in mmap.path:
                logger.warning(
                    "Non-gProfiler async-profiler is already loaded to the target process."
                    f" (disable --java-safemode={JavaSafemodeOptions.AP_LOADED_CHECK} to bypass this check)",
                    pid=process.pid,
                    ap_path=mmap.path,
                )
                return True

        return False

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        comm = process_comm(process)
        exe = process_exe(process)
        java_version_output: Optional[str] = get_java_version_logged(process, self._profiler_state.stop_event)

        if self._enabled_proc_events_java:
            self._want_to_profile_pids.add(process.pid)
            # there's no reliable way to get the underlying cache of get_java_version, otherwise
            # I'd just use it.
            if len(self._pid_to_java_version) > JAVA_VERSION_CACHE_MAX:
                self._pid_to_java_version.clear()

            # This Java version might be used in _proc_exit_callback
            self._pid_to_java_version[process.pid] = java_version_output

        if self._safemode_disable_reason is not None:
            return self._profiling_skipped_profile(f"disabled due to {self._safemode_disable_reason}", comm)

        if not self._is_jvm_profiling_supported(process, exe, java_version_output):
            return self._profiling_skipped_profile("profiling this JVM is not supported", comm)

        if self._check_async_profiler_loaded(process):
            return self._profiling_skipped_profile("async-profiler is already loaded", comm)

        # track profiled PIDs only if proc_events are in use, otherwise there is no use in them.
        # TODO: it is possible to run in contexts where we're unable to use proc_events but are able to listen
        # on kernel messages. we can add another mechanism to track PIDs (such as, prune PIDs which have exited)
        # then use the kernel messages listener without proc_events.
        if self._enabled_proc_events_java:
            self._profiled_pids.add(process.pid)

        logger.info(f"Profiling{' spawned' if spawned else ''} process {process.pid} with async-profiler")
        container_name = self._profiler_state.get_container_name(process.pid)
        app_metadata = self._metadata.get_metadata(process)
        appid = application_identifiers.get_java_app_id(process, self._collect_spark_app_name)

        if is_diagnostics():
            execfn = (app_metadata or {}).get("execfn")
            logger.debug("Process paths", pid=process.pid, execfn=execfn, exe=exe)
            logger.debug("Process mapped files", pid=process.pid, maps=set(m.path for m in process.memory_maps()))

        with AsyncProfiledProcess(
            process,
            self._profiler_state.stop_event,
            self._profiler_state.storage_dir,
            self._profiler_state.insert_dso_name,
            asprof_path(),
            get_ap_version(),
            os.path.join("java", "glibc", "libasyncProfiler.so"),
            os.path.join("java", "musl", "libasyncProfiler.so"),
            self._mode,
            self._ap_safemode,
            self._ap_features,
            self._ap_args,
            logger,
            self._jattach_timeout,
            self._ap_mcache,
            self._report_meminfo,
            self._include_method_modifiers,
            self._java_line_numbers,
        ) as ap_proc:
            stackcollapse = self._profile_ap_process(ap_proc, comm, duration)

        return ProfileData(stackcollapse, appid, app_metadata, container_name)

    @staticmethod
    def _log_mem_usage(ap_log: str, pid: int) -> None:
        match = MEM_INFO_LOG_RE.search(ap_log)
        if match is None:
            logger.warning("Couldn't extract mem usage from ap log", log=ap_log, pid=pid)
            return

        call_trace, dictionaries, code_cache, total = [int(raw) * 1024 for raw in match.groups()]
        logger.debug(
            "async-profiler memory usage (in bytes)",
            mem_total=total,
            mem_call_trace=call_trace,
            mem_dictionaries=dictionaries,
            mem_code_cache=code_cache,
            pid=pid,
        )

    def _profile_ap_process(self, ap_proc: AsyncProfiledProcess, comm: str, duration: int) -> StackToSampleCount:
        started = ap_proc.start_async_profiler(self._interval, ap_timeout=self._ap_timeout)
        if not started:
            logger.info(f"Found async-profiler already started on {ap_proc.process.pid}, trying to stop it...")
            # stop, and try to start again. this might happen if AP & gProfiler go out of sync: for example,
            # gProfiler being stopped brutally, while AP keeps running. If gProfiler is later started again, it will
            # try to start AP again...
            # not using the "resume" action because I'm not sure it properly reconfigures all settings; while stop;start
            # surely does.
            ap_proc.stop_async_profiler(with_output=False)
            started = ap_proc.start_async_profiler(self._interval, second_try=True, ap_timeout=self._ap_timeout)
            if not started:
                raise Exception(
                    f"async-profiler is still running in {ap_proc.process.pid}, even after trying to stop it!"
                )

        try:
            wait_event(
                duration, self._profiler_state.stop_event, lambda: not is_process_running(ap_proc.process), interval=1
            )
        except TimeoutError:
            # Process still running. We will stop the profiler in finally block.
            pass
        else:
            # Process terminated, was it due to an error?
            self._check_hotspot_error(ap_proc)
            logger.debug(f"Profiled process {ap_proc.process.pid} exited before stopping async-profiler")
            # fall-through - try to read the output, since async-profiler writes it upon JVM exit.
        finally:
            if is_process_running(ap_proc.process):
                ap_log = ap_proc.stop_async_profiler(True)
                if self._report_meminfo:
                    self._log_mem_usage(ap_log, ap_proc.process.pid)

        output = ap_proc.read_output()
        if output is None:
            logger.warning(f"Profiled process {ap_proc.process.pid} exited before reading the output")
            return self._profiling_error_stack("error", "process exited before reading the output", comm)
        else:
            logger.info(f"Finished profiling process {ap_proc.process.pid}")
            return parse_one_collapsed(output, comm)

    def _check_hotspot_error(self, ap_proc: AsyncProfiledProcess) -> None:
        pid = ap_proc.process.pid
        error_file = ap_proc.locate_hotspot_error_file()
        if not error_file:
            logger.debug(f"No Hotspot error log for pid {pid}")
            return

        contents = open(error_file).read()
        msg = "Found Hotspot error log"
        if not self._java_full_hserr:
            m = VM_INFO_REGEX.search(contents)
            vm_info = m[1] if m else ""
            m = SIGINFO_REGEX.search(contents)
            siginfo = m[1] if m else ""
            m = PROBLEMATIC_FRAME_REGEX.search(contents)
            problematic_frame = m[1] if m else ""
            m = NATIVE_FRAMES_REGEX.search(contents)
            native_frames = m[1] if m else ""
            m = CONTAINER_INFO_REGEX.search(contents)
            container_info = m[1] if m else ""
            contents = (
                f"VM info: {vm_info}\n"
                + f"siginfo: {siginfo}\n"
                + f"Problematic frame: {problematic_frame}\n"
                + f"native frames:\n{native_frames}\n"
                + f"container info:\n{container_info}"
            )
        logger.warning(msg, pid=pid, hs_err_file=error_file, hs_err=contents)

        self._disable_profiling(JavaSafemodeOptions.HSERR)

    def _select_processes_to_profile(self) -> List[Process]:
        if self._safemode_disable_reason is not None:
            logger.debug("Java profiling has been disabled, skipping profiling of all java processes")
            # continue - _profile_process will return an appropriate error for each process selected for
            # profiling.
        return pgrep_maps(DETECTED_JAVA_PROCESSES_REGEX)

    def _should_profile_process(self, process: Process) -> bool:
        return search_proc_maps(process, DETECTED_JAVA_PROCESSES_REGEX) is not None

    def start(self) -> None:
        super().start()
        try:
            proc_events.register_exit_callback(self._proc_exit_callback)
        except Exception:
            logger.warning(
                "Failed to enable proc_events listener for exited Java processes"
                " (this does not prevent Java profiling)",
                exc_info=True,
            )
        else:
            self._enabled_proc_events_java = True

    def stop(self) -> None:
        if self._enabled_proc_events_java:
            proc_events.unregister_exit_callback(self._proc_exit_callback)
            self._enabled_proc_events_java = False
        super().stop()

    def _proc_exit_callback(self, tid: int, pid: int, exit_code: int) -> None:
        # Notice that we only check the exit code of the main thread here.
        # It's assumed that an error in any of the Java threads will be reflected in the exit code of the main thread.
        if tid in self._want_to_profile_pids:
            self._pids_to_remove.add(tid)
            java_version_output = self._pid_to_java_version.get(tid)

            signo = java_exit_code_to_signo(exit_code)
            if signo is None:
                # not a signal, do not report
                return

            if tid in self._profiled_pids:
                logger.warning(
                    "async-profiled Java process exited with signal",
                    pid=tid,
                    signal=signo,
                    java_version_output=java_version_output,
                )

                if is_java_fatal_signal(signo):
                    self._disable_profiling(JavaSafemodeOptions.PROFILED_SIGNALED)
            else:
                # this is a process that we wanted to profile, but didn't profile due to safemode / any other reason.
                logger.debug(
                    "Non-profiled Java process exited with signal",
                    pid=tid,
                    signal=signo,
                    java_version_output=java_version_output,
                )

    def _handle_kernel_messages(self, messages: List[KernelMessage]) -> None:
        for message in messages:
            _, _, text = message
            oom_entry = get_oom_entry(text)
            if oom_entry and oom_entry.pid in self._profiled_pids:
                logger.warning("Profiled Java process OOM", oom=json.dumps(oom_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.PROFILED_OOM)
                continue

            signal_entry = get_signal_entry(text)
            if signal_entry is not None and signal_entry.pid in self._profiled_pids:
                logger.warning("Profiled Java process fatally signaled", signal=json.dumps(signal_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.PROFILED_SIGNALED)
                continue

            # paranoia - in safemode, stop Java profiling upon any OOM / fatal-signal / occurrence of a profiled
            # PID in a kernel message.
            if oom_entry is not None:
                logger.warning("General OOM", oom=json.dumps(oom_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.GENERAL_OOM)
            elif signal_entry is not None:
                logger.warning("General signal", signal=json.dumps(signal_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.GENERAL_SIGNALED)
            elif any(str(p) in text for p in self._profiled_pids):
                logger.warning("Profiled PID shows in kernel message line", line=text)
                self._disable_profiling(JavaSafemodeOptions.PID_IN_KERNEL_MESSAGES)

    def _handle_new_kernel_messages(self) -> None:
        try:
            messages = list(self._kernel_messages_provider.iter_new_messages())
        except Exception:
            logger.exception("Error iterating new kernel messages")
        else:
            self._handle_kernel_messages(messages)

    def snapshot(self) -> ProcessToProfileData:
        try:
            return super().snapshot()
        finally:
            self._handle_new_kernel_messages()
            self._profiled_pids -= self._pids_to_remove
            self._want_to_profile_pids -= self._pids_to_remove
            for pid in self._pids_to_remove:
                self._pid_to_java_version.pop(pid, None)
            self._pids_to_remove.clear()
