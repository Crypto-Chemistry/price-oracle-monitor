FROM python:3.10-alpine AS base
RUN apk update && apk upgrade && \
    apk add --no-cache git
RUN git clone https://github.com/Crypto-Chemistry/price-oracle-monitor.git /ccpom
WORKDIR /ccpom
RUN pip3 install -r requirements.txt

ENTRYPOINT [ "python3" ]
