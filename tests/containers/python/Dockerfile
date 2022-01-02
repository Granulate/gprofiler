FROM python:3.6-slim

WORKDIR /app
ADD lister.py /app
# Install some package so we can test that its info appears in the collapsed
RUN pip install pyyaml==6.0

CMD ["python", "lister.py"]
