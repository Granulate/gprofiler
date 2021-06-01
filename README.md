# gProfiler
gProfiler combines multiple sampling profilers to produce unified visualization of
what your CPU is spending time on, displaying stack traces of your processes
across native programs<sup id="a1">[1](#perf-native)</sup> (includes Golang), Java and Python runtimes, and kernel routines.

gProfiler can upload its results to the [Granulate Performance Studio](https://profiler.granulate.io/), which aggregates the results from different instances over different periods of time and can give you a holistic view of what is happening on your entire cluster.
To upload results, you will have to register and generate a token on the website.

gProfiler runs on Linux.

![Granulate Performance Studio example view](https://github.com/Granulate/gprofiler/blob/master/images/studio.png?raw=true)

# Running

This section describes the possible options to control gProfiler's output, and the various execution modes (as a container, as an executable, etc...)

## Output options

gProfiler can produce output in two ways:

* Create an aggregated, collapsed stack samples file (`profile_<timestamp>.col`)
  and a flamegraph file (`profile_<timestamp>.html`). Two symbolic links (`last_profile.col` and `last_flamegraph.html`) always point to the last output files.

  Use the `--output-dir`/`-o` option to specify the output directory.

  If `--rotating-output` is given, only the last results are kept (available via `last_profle.col` and `last_flamegraph.html`). This can be used to avoid increasing gProfiler's disk usage over time. Useful in conjunction with `--upload-results` (explained ahead) - historical results are available in the Granulate Performance Studio, and the very latest results are available locally.

  `--no-flamegraph` can be given to avoid generation of the `profile_<timestamp>.html` file - only the collapsed stack samples file will be created.

* Send the results to the Granulate Performance Studio for viewing online with
  filtering, insights, and more.

  Use the `--upload-results`/`-u` flag. Pass the `--token` option to specify the token
  provided by Granulate Performance Studio, and the `--service-name` option to specify an identifier
  for the collected profiles, as will be viewed in the [Granulate Performance Studio](https://profiler.granulate.io/). *Profiles sent from numerous
  gProfilers using the same service name will be aggregated together.*

Note: both flags can be used simultaneously, in which case gProfiler will create the local files *and* upload
the results.

## Profiling options
* `--profiling-frequency`: The sampling frequency of the profiling, in *hertz*.
* `--profiling-duration`: The duration of the each profiling session, in *seconds*.
* `--profiling-interval`: The interval between each profiling session, in *seconds*.

The default profiling frequency is *11 hertz*. Using higher frequency will lead to more accurate results, but will create greater overhead on the profiled system & programs.

The default duration is *60 seconds*, and the default interval matches it. So gProfiler runs the profiling sessions back-to-back - the next session starts as soon as the previous session is done.

* `--no-java`, `--no-python`: Disable the runtime-specific profilers of Java and/or Python, accordingly.

### Continuous mode
gProfiler can be run in a continuous mode, profiling periodically, using the `--continuous`/`-c` flag.
Note that when using `--continuous` with `--output-dir`, a new file will be created during *each* sampling interval.
Aggregations are only available when uploading to the Granulate Performance Studio.

## Running as a Docker container
Run the following to have gProfiler running continuously, uploading to Granulate Performance Studio:
```bash
docker pull granulate/gprofiler:latest
docker run --name gprofiler -d --restart=always \
    --network=host --pid=host --userns=host --privileged \
    -v /lib/modules:/lib/modules:ro -v /usr/src:/usr/src:ro \
    -v /var/run/docker.sock:/var/run/docker.sock \
	granulate/gprofiler:latest -cu --token <token> --service-name <service> [options]
```

For profiling with eBPF, kernel headers must be accessible from within the container at
`/lib/modules/$(uname -r)/build`. On Ubuntu, this directory is a symlink pointing to `/usr/src`.
The command above mounts both of these directories.

## Running as an executable
Run the following to have gprofiler running continuously, uploading to Granulate Performance Studio:
```bash
wget https://github.com/Granulate/gprofiler/releases/latest/download/gprofiler
sudo chmod +x gprofiler
sudo ./gprofiler -cu --token <token> --service-name <service> [options]
```

gProfiler unpacks executables to `/tmp` by default; if your `/tmp` is marked with `noexec`,
you can add `TMPDIR=/proc/self/cwd` to have everything unpacked in your current working directory.
```bash
sudo TMPDIR=/proc/self/cwd ./gprofiler -cu --token <token> --service-name <service> [options]
```

#### Executable known issues
The following platforms are currently not supported with the gProfiler executable:
+ Ubuntu 14.04
+ Alpine

**Remark:** container-based execution works and can be used in those cases.

## Running as a Kubernetes DaemonSet
See [gprofiler.yaml](deploy/k8s/gprofiler.yaml) for a basic template of a DaemonSet running gProfiler.
Make sure to insert the `GPROFILER_TOKEN` and `GPROFILER_SERVICE` variables in the appropriate location!

## Running from source
gProfiler requires Python 3.6+ to run.

```bash
pip3 install -r requirements.txt
./scripts/build.sh
```

Then, run the following **as root**:
```bash
python3 -m gprofiler [options]
```

# Theory of operation
Each profiling interval, gProfiler invokes `perf` in system wide mode, collecting profiling data for all running processes.
Alongside `perf`, gProfiler invokes runtime-specific profilers for processes based on these environments:
* Java runtimes (version 7+) based on the HotSpot JVM, including the Oracle JDK and other builds of OpenJDK like AdoptOpenJDK and Azul Zulu.
  * Uses async-profiler.
* The CPython interpreter, versions 2.7 and 3.5-3.9.
  * eBPF profiling (based on PyPerf) requires Linux 4.14 or higher. Profiling using eBPF incurs lower overhead. This requires kernel headers to be installed.
  * If eBPF is not available for whatever reason, py-spy is used.
* PHP (Zend Engine), versions 7.0-8.0.
  * Uses [Granulate's fork](https://github.com/Granulate/phpspy/) of the phpspy project.

The runtime-specific profilers produce stack traces that include runtime information (i.e, stacks of Java/Python functions), unlike `perf` which produces native stacks of the JVM / CPython interpreter.
The runtime stacks are then merged into the data collected by `perf`, substituting the *native* stacks `perf` has collected for those processes.

# Contribute
We welcome all feedback and suggestion through Github Issues:
* [Submit bugs and feature requests](https://github.com/granulate/gprofiler/issues)
* Upvote [popular feature requests](https://github.com/granulate/gprofiler/issues?q=is%3Aopen+is%3Aissue+label%3Aenhancement+sort%3Areactions-%2B1-desc+)

## Releasing a new version
1. Update `__version__` in `__init__.py`.
2. Create a tag with the same version (after merging the `__version__` update) and push it.

We recommend going through our [contribution guide](https://github.com/granulate/gprofiler/blob/master/CONTRIBUTING.md) for more details.

# Credits
* [async-profiler](https://github.com/jvm-profiling-tools/async-profiler) by [Andrei Pangin](https://github.com/apangin). See [our fork](https://github.com/Granulate/async-profiler).
* [py-spy](https://github.com/benfred/py-spy) by [Ben Frederickson](https://github.com/benfred). See [our fork](https://github.com/Granulate/py-spy).
* [bcc](https://github.com/iovisor/bcc) (for PyPerf) by the IO Visor project. See [our fork](https://github.com/Granulate/bcc).
* [phpspy](https://github.com/adsr/phpspy) by [Adam Saponara](https://github.com/adsr). See [our fork](https://github.com/Granulate/phpspy).

# Footnotes

<a name="perf-native">1</a>: *Currently* requires profiled native programs to be compiled with frame pointer. [↩](#a1)
