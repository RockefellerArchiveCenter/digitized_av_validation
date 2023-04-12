FROM python:3.10-slim-buster as base
RUN apt-get update && apt-get install -y mediaconch
WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY validate.py .

FROM base as test
COPY test_requirements.txt .coveragerc ./
RUN pip install -r test_requirements.txt
COPY fixtures fixtures
COPY test_validate.py .

FROM base as build
CMD [ "python", "validate.py" ]