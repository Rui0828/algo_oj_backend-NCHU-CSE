FROM alpine:3.19 AS downloader

WORKDIR /app

RUN <<EOS
set -ex
apk add unzip
wget https://github.com/IdONTKnowCHEK/OnlineJudgeFE-NCHU/releases/download/frontend/dist.zip
unzip dist.zip
rm -f dist.zip
EOS

FROM python:3.12-alpine
ARG TARGETARCH
ARG TARGETVARIANT

ENV OJ_ENV production
WORKDIR /app

ARG USERNAME
ARG PASSWORD

COPY ./deploy/requirements.txt /app/deploy/
# psycopg2: libpg-dev
# pillow: libjpeg-turbo-dev zlib-dev freetype-dev
RUN --mount=type=cache,target=/etc/apk/cache,id=apk-cache-$TARGETARCH$TARGETVARIANT-final \
    --mount=type=cache,target=/root/.cache/pip,id=pip-cache-$TARGETARCH$TARGETVARIANT-final \
    <<EOS
set -ex
apk add gcc libc-dev python3-dev libpq libpq-dev libjpeg-turbo libjpeg-turbo-dev zlib zlib-dev freetype freetype-dev supervisor openssl nginx curl unzip openssh
pip install -r /app/deploy/requirements.txt
apk del gcc libc-dev python3-dev libpq-dev libjpeg-turbo-dev zlib-dev freetype-dev
EOS

# 設定 SSH 服務
RUN <<EOS
set -ex
ssh-keygen -A
echo "AllowTcpForwarding yes" >> /etc/ssh/sshd_config
echo "GatewayPorts yes" >> /etc/ssh/sshd_config
EOS

# 建立用戶，並設定密碼、權限
RUN <<EOS
set -ex
adduser -D -h /app ${USERNAME}
echo "${USERNAME}:${PASSWORD}" | chpasswd
mkdir -p /data
chown -R ${USERNAME}:${USERNAME} /app /data
chmod -R 770 /app /data
EOS

COPY ./ /app/
# COPY --from=downloader --link /app/dist/ /app/dist/
RUN chmod -R u=rwX,go=rX ./ && chmod +x ./deploy/entrypoint.sh

HEALTHCHECK --interval=5s CMD [ "/usr/local/bin/python3", "/app/deploy/health_check.py" ]
EXPOSE 8000 22

# 確保 SSH 服務在 container 啟動時執行
ENTRYPOINT [ "/app/deploy/entrypoint.sh" ]
