FROM python:3.10

# RUN yum update -y && yum install -y \
#   make gcc curl gpg which tar procps wget \
#   git

WORKDIR /code

ADD requirements.txt .
RUN pip install -r requirements.txt

COPY validate.py .

CMD [ "validate.main" ]