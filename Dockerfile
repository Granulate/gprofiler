FROM ubuntu:20.04

WORKDIR /app

RUN apt-get update && apt-get install -y curl python3-pip

COPY build.sh build.sh
RUN ./build.sh

COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py ./
COPY gprofiler gprofiler
RUN python3 setup.py install

STOPSIGNAL SIGINT

ENTRYPOINT [ "python3", "-m", "gprofiler", "-v" ]
