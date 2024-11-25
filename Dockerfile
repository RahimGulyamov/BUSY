FROM python:3.10

RUN apt-get -y update
RUN apt-get -y upgrade
RUN apt-get install -y ffmpeg

ENV BUSY_HOME=/opt/busy
RUN mkdir -p $BUSY_HOME

RUN pip install --upgrade pip
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY app $BUSY_HOME/app
COPY tests $BUSY_HOME/tests
COPY config_*.py $BUSY_HOME/

WORKDIR $BUSY_HOME
CMD python -m app --config config_local.py

EXPOSE 8000/tcp