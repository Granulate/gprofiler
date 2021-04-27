FROM ubuntu:20.04 as bcc-builder

RUN apt-get update

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential iperf llvm-9-dev libclang-9-dev \
  cmake python3 flex bison libelf-dev libz-dev

WORKDIR /bcc

RUN git clone --depth 1 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 119d71bf9681182759eb76d40660c0ec19f3fc42
RUN mkdir bcc/build && cd bcc/build && \
  cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root .. && \
  make -C examples/cpp/pyperf -j -l VERBOSE=1 install


FROM ubuntu:20.04 as python-prctl-builder
RUN apt-get update && apt-get install -y python3-pip gcc libcap-dev python3-dev
RUN pip3 install --upgrade pip setuptools wheel
# Dockerfile doesn't allow glob in COPY and pip requires the full wheel filename, so move to a directory
RUN pip3 wheel python-prctl==1.8.1 && mkdir dist && mv python_prctl-*.whl dist/


FROM ubuntu:20.04

WORKDIR /app

# kmod - for modprobe kheaders if it's available
# libcap2 - for python-prctl
RUN apt-get update && apt-get install -y curl python3-pip kmod libcap2

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
COPY --from=bcc-builder /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
COPY --from=bcc-builder /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
COPY --from=bcc-builder /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/
COPY --from=python-prctl-builder dist prctl

COPY scripts/build.sh scripts/build.sh
RUN ./scripts/build.sh

COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir ./prctl/*.whl -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py ./
COPY gprofiler gprofiler
RUN python3 setup.py install

# lets gProfiler know it is running in a container
ENV GPROFILER_IN_CONTAINER=1

STOPSIGNAL SIGINT

ENTRYPOINT [ "python3", "-m", "gprofiler" ]
