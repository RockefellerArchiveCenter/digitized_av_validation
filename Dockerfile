FROM python:3.11-alpine AS base

# Install base system requirements
RUN apk add --no-cache mediaconch

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