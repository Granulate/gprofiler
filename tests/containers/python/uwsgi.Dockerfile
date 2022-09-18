ARG PYTHON_IMAGE_TAG
FROM python:${PYTHON_IMAGE_TAG}

WORKDIR /app

RUN pip install uwsgi

ADD lister.py /app
CMD ["uwsgi", "--py", "lister.py"]
