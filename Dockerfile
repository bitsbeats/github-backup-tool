FROM python:3.11-alpine


RUN apk update
RUN apk add git sqlite

WORKDIR /github-backup

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python", "cli.py", "-c", "/work/config.yaml" ]