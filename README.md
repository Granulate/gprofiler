# gProfiler
gProfiler combines multiple sampling profilers to produce unified visualization of
what your CPU is spending time on, displaying stack traces of your processes
across native programs, Java and Python runtimes, and kernel routines.

gProfiler can upload the result to the [Granulate Performance Studio](https://profiler.granulate.io/), which aggregates the results from different instances over different periods of time and can give you a holistic view of what is happening on your entire cluster.
To upload results, you will have to register and generate a token on the website.

## Requirements
gProfiler works on **Linux** and requires **Python 3.6+** to run.

The `nsenter` program needs to be installed for Java profiling. For Debian/Ubuntu, install the `util-linux` package.

It can produce specialized stack traces for the following runtimes:
* Java runtimes (version 7+) based on the HotSpot JVM,
including the Oracle JDK and other builds of OpenJDK like AdoptOpenJDK and Azul Zulu.
* The CPython interpreter, versions 2.7 and 3.5-3.9.
  * eBPF profiling requires Linux 4.14 or higher.

gProfiler can profile Python applications with low overhead using eBPF. This requires kernel
headers to be installed.

## Running from source
```bash
pip3 install -r requirements.txt
./scripts/build.sh
```

### Usage
Run the following **as root**:
```bash
python3 -m gprofiler [options]
```

gProfiler can produce output in two ways:

* Create an aggregated, collapsed stack samples file (`profile_<timestamp>.col`)
  and a flamegraph file (`profile_<timestamp>.html`).

  Use the `--output-dir`/`-o` option to specify the output directory.

* Send the results to the Granulate Performance Studio for viewing online with
  filtering, insights, and more.

  Use the `--upload-results`/`-u` flag. Pass the `--token` option to specify the token
  provided by Granulate Performance Studio, and the `--service-name` option to specify an identifier
  for the collected profiles, as will be viewed in the Granulate Performance Studio. Profiles sent from numerous
  gProfilers using the same service name will be aggregated together.

Note: both flags can be used simultaneously, in which case gProfiler will create the local files *and* upload
the results.

## Running as a docker container
Run the following to have gProfiler running continuously, uploading to Granulate Performance Studio:
```bash
docker pull granulate/gprofiler:latest
docker run --name gprofiler -d --restart=always \
    --network=host --pid=host --userns=host --privileged \
    -v /lib/modules:/lib/modules:ro -v /usr/src:/usr/src:ro \
    granulate/gprofiler:latest -cu --token <token> [options]
```

For eBPF profiling, kernel headers must be accessible from within the container at
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

## Profiling options
* `--profiling-frequency`: The sampling frequency of the profiling, in hertz.
* `--profiling-duration`: The duration of the each profiling session, in seconds.
* `--profiling-interval`: The interval between each profiling session, in minutes.

By default, the duration is 60 seconds and the interval is 1 minute. So gProfiler runs the profiling
sessions back-to-back.

### Continuous mode
gProfiler can be run in a continuous mode, profiling periodically,
using the `--continuous`/`-c` flag and specifying the period using the `--profiling-interval` option.
Note that when using `--continuous` with `--output-dir`, a new file will be created during *each* sampling interval.
Aggregations are only available when uploading to the [Granulate Performance Studio](https://profiler.granulate.io/)

# Contribute
We welcome all feedback and suggestion through Github Issues:
* [Submit bugs and feature requests](https://github.com/granulate/gprofiler/issues)
* Upvote [popular feature requests](https://github.com/granulate/gprofiler/issues?q=is%3Aopen+is%3Aissue+label%3Aenhancement+sort%3Areactions-%2B1-desc+)

## Releasing a new version
1. Update `__version__` in `__init__.py`.
2. Create a tag with the same version (after merging the `__version__` update) and push it.

We recommend going through our [contribution guide](https://github.com/granulate/gprofiler/blob/master/CONTRIBUTING.md) for more details.

# Credits
* [async-profiler](https://github.com/jvm-profiling-tools/async-profiler) by [Andrei Pangin](https://github.com/apangin)
* [py-spy](https://github.com/benfred/py-spy) by [Ben Frederickson](https://github.com/benfred)
