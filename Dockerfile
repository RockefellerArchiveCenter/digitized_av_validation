FROM python:3.10-slim-buster as base
RUN apt-get update && apt-get install -y mediaconch
WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src src

FROM base as test
COPY test_requirements.txt .coveragerc ./
RUN pip install -r test_requirements.txt
COPY tests tests

FROM base as build
CMD [ "python", "src/validate.py" ]