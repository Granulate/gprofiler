FROM python:3.6-alpine

WORKDIR /app
ADD lister.py /app
# yaml is used in lister.py
RUN pip install pyyaml==6.0
# this is used to test that we identify Python processes to profile based on "libpython" in their "/proc/pid/maps".
# so we'll run a Python script using non-"python" executable ("shmython" instead) but it'll have "libpython"
# loaded.
RUN ln /usr/local/bin/python3.6 /usr/local/bin/shmython && ! test -L /usr/local/bin/shmython && ldd /usr/local/bin/shmython | grep libpython > /dev/null

CMD ["/usr/local/bin/shmython", "/app/lister.py"]
