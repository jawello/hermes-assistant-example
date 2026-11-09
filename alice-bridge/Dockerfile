FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

COPY . /opt/alice-bridge
RUN pip install --no-cache-dir /opt/alice-bridge

EXPOSE 8000

CMD ["alice-bridge"]
