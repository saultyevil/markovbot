# syntax=docker/dockerfile:1

FROM python:3.11-slim-buster

# Create markovbot user, required for ssh key shenanigans and update command
RUN useradd -m markovbot
RUN mkdir -p /home/markovbot/.ssh
RUN chown -R markovbot:markovbot /home/markovbot/.ssh

# Install git and openssh, required for update command
RUN apt update && apt install -y git openssh-client

# This enables git to be OK with adding this directory, so we can use the
# git update command
RUN git config --global --add safe.directory /bot

# Install poetry via pip
USER markovbot
RUN pip install poetry
ENV PATH="/home/markovbot/.local/bin:$PATH"

# Copy the poetry files to cache them in docker layer
WORKDIR /bot
COPY poetry.lock pyproject.toml README.md ./
RUN poetry install --no-interaction --no-ansi --no-root

# Switch to markovbot and run bot
CMD ["./entrypoint.sh"]
