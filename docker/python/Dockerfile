FROM python:2.7
MAINTAINER Chuck "MANCHUCK" Reeves <chuck@manchuck.com>

COPY deploy.py /deploy.py
COPY deploy_games.py /deploy_games.py
COPY deploy_to_s3.py /deploy_to_s3.py

RUN pip install boto3 python-magic

CMD ["echo 'hello'"]