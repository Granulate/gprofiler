FROM python:3.6

WORKDIR /gprofiler
RUN apt-get -qq update

# Installing openjdk-8 on Debian requires adding apt repository
RUN apt-get install -y apt-transport-https ca-certificates wget dirmngr gnupg software-properties-common -y
RUN wget -qO - https://adoptopenjdk.jfrog.io/adoptopenjdk/api/gpg/key/public | apt-key add -
RUN add-apt-repository --yes https://adoptopenjdk.jfrog.io/adoptopenjdk/deb/
RUN apt update -y
RUN apt install adoptopenjdk-8-hotspot -y

RUN apt-get install -y -qq --no-install-recommends python3 python3-pip docker.io php

RUN pip install -q --upgrade setuptools pip
RUN pip install -q pytest docker

ADD . /gprofiler
RUN ./scripts/build.sh
RUN pip install .
CMD ["pytest", "-v", "tests/", "--ignore=tests/test_executable.py"]