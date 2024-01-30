FROM python:3.11 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.11 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

# FROM ubuntu:hirsute
FROM debian:bookworm-slim
WORKDIR /app
RUN apt update
RUN apt-get install -y python3.11 python3-pip --fix-missing
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/
COPY --from=libbuilder /app/venv/lib/python3.11/site-packages /app/
COPY ./faebot.py /app/
WORKDIR /app
ENTRYPOINT ["/usr/bin/python3.11", "/app/faebot.py"]
