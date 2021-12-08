FROM python:3.6.15-slim

WORKDIR /app
ADD lister.py /app
# Install some package so we can test that its info appears in the collapsed
RUN pip install setuptools==59.4.0

CMD ["/app/lister.py"]
