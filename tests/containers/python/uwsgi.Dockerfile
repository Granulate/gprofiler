ARG PYTHON_IMAGE_TAG
FROM python:${PYTHON_IMAGE_TAG}

WORKDIR /app

# to build uwsgi
RUN if grep -q Alpine /etc/os-release; then apk add gcc libc-dev linux-headers; fi

RUN pip install uwsgi

ADD lister.py /app
CMD ["uwsgi", "--py", "lister.py"]
