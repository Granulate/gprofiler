# pinned python:3.6-slim
FROM python@sha256:2cfebc27956e6a55f78606864d91fe527696f9e32a724e6f9702b5f9602d0474

WORKDIR /app
ADD lister.py /app
# Install some package so we can test that its info appears in the collapsed
RUN pip install pyyaml==6.0

CMD ["python", "lister.py"]
