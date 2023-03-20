FROM python:3.10-slim

WORKDIR /code

ADD requirements.txt .
RUN pip install -r requirements.txt

COPY validate.py .

CMD [ "validate.main" ]