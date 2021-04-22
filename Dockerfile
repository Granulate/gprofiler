FROM ubuntu:20.04 as bcc-builder

RUN apt-get update

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential iperf llvm-9-dev libclang-9-dev \
  cmake python3 flex bison libelf-dev libz-dev

WORKDIR /bcc

RUN git clone --depth 1 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 119d71bf9681182759eb76d40660c0ec19f3fc42
RUN mkdir bcc/build && cd bcc/build && \
  cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root .. && \
  make -C examples/cpp/pyperf -j -l VERBOSE=1 install


FROM ubuntu:20.04

WORKDIR /app

# kmod - for modprobe kheaders if it's available
RUN apt-get update && apt-get install -y curl python3-pip kmod

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
COPY --from=bcc-builder /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
COPY --from=bcc-builder /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
COPY --from=bcc-builder /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/

COPY scripts/build.sh scripts/build.sh
RUN ./scripts/build.sh

COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py ./
COPY gprofiler gprofiler
RUN python3 setup.py install

STOPSIGNAL SIGINT

ENTRYPOINT [ "python3", "-m", "gprofiler", "-v" ]
