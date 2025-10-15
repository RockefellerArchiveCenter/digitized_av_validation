FROM python:3.11-alpine AS base
RUN apt-get update && apt-get install -y mediaconch
WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src src
COPY mediaconch mediaconch

FROM base AS test
COPY test_requirements.txt .coveragerc ./
RUN pip install -r test_requirements.txt
COPY tests tests

FROM base AS build
CMD [ "python", "src/validate.py" ]