ARG PYTHON_IMAGE_TAG
FROM python:${PYTHON_IMAGE_TAG}

WORKDIR /app
ADD lister.py /app
CMD ["python", "lister.py"]
