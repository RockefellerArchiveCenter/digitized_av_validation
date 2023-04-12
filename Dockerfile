FROM python:3.10-slim-buster

RUN apt-get update && apt-get install -y mediaconch

WORKDIR /code

ADD requirements.txt .
RUN pip install -r requirements.txt

COPY validate.py .

CMD [ "validate.main" ]