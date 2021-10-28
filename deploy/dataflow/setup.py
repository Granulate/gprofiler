#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import subprocess
from distutils.command.build import build as _build  # type: ignore

import setuptools

# ------------------------------------------------------------------------------------------------------------
# TODO: replace with the actual service name you wish to use
SERVICE_NAME = "<SERVICE NAME>"
# TODO: replace with a token you got from the performance studio (https://profiler.granulate.io/installation)
GPROFILER_TOKEN = "<TOKEN>"
# ------------------------------------------------------------------------------------------------------------

assert SERVICE_NAME != "<SERVICE NAME>", "Please update the SERVICE_NAME value with an appropriate service name"
assert (
    GPROFILER_TOKEN != "<TOKEN>"
), "Please update the GPROFILER_TOKEN value with an appropriate Performance Studio token"

KEEP_LOGS = True


class build(_build):
    sub_commands = _build.sub_commands + [("ProfilerInstallationCommands", None)]


COMMANDS = [
    "wget -q https://github.com/Granulate/gprofiler/releases/latest/download/gprofiler_$(uname -m) -O /tmp/gprofiler",
    "sudo chmod +x /tmp/gprofiler",
    f"sudo setsid /tmp/gprofiler -cu --token {GPROFILER_TOKEN} --service-name {SERVICE_NAME}"
    f" --disable-pidns-check > {'/tmp/gprofiler.log' if KEEP_LOGS else '/dev/null'} 2>&1 &",
]


class ProfilerInstallationCommands(setuptools.Command):
    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    @staticmethod
    def run_command(command):
        print("Running command: %s" % command)
        p = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True
        )
        stdout_data, _ = p.communicate(input=b'\n')
        print("Command output: %s" % stdout_data)
        if p.returncode != 0:
            raise RuntimeError("Command %s failed: exit code: %s" % (command, p.returncode))

    def run(self):
        for command in COMMANDS:
            self.run_command(command)


setuptools.setup(
    name="gprofiler_installer",
    version="0.0.1",
    author="Granulate",
    author_email="",  # TODO
    url="https://github.com/Granulate/gprofiler",
    description="gProfiler installer package",
    cmdclass={
        "build": build,
        "ProfilerInstallationCommands": ProfilerInstallationCommands,
    },
)
