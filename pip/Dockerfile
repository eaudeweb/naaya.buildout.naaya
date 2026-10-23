# Image of a Naaya site, built from the site directory (site.cfg, after make bootstrap):
#   docker build -f src/naaya.buildout.naaya/pip/Dockerfile -t naaya-<site> .
FROM python:3.14-slim-trixie AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git make \
        libldap2-dev libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
# Empty secrets for the build; the real secret.cfg is mounted when the container runs
RUN grep -o '${secrets:[A-Za-z0-9_-]*}' site.cfg | sort -u \
        | sed 's/^${secrets:\(.*\)}$/\1 =/' | (echo '[secrets]'; cat) > secret.cfg \
    && make requirements packages PYTHON=python3.14 \
    && rm -rf /root/.cache


FROM python:3.14-slim-trixie

# The user owning the site directory on the host, so the containers can write var/
ARG UID=500
ARG GID=500

# ffmpeg: media files, pdftotext and catdoc: full-text index, Pango and HarfBuzz: WeasyPrint
RUN apt-get update \
    && apt-get install -y --no-install-recommends make haproxy ffmpeg \
        poppler-utils catdoc libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        libldap2 libsasl2-2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g $GID zope && useradd -u $UID -g $GID -m zope

COPY --from=build --chown=zope:zope /app /app
WORKDIR /app
USER zope

ENTRYPOINT ["src/naaya.buildout.naaya/pip/docker-entrypoint.sh"]
