FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*
# CLI versions pinned per docs/cli-probe.md (Cursor installer is unpinned)
RUN npm i -g @anthropic-ai/claude-code@2.1.322 @openai/codex@0.167.1 @google/gemini-cli@0.68.0
RUN useradd -m -u 10001 -d /home/ayaan ayaan && mkdir -p /data && chown ayaan:ayaan /data
USER ayaan
ENV HOME=/home/ayaan PATH=/home/ayaan/.local/bin:$PATH
RUN curl https://cursor.com/install -fsS | bash
USER root
WORKDIR /app
COPY pyproject.toml ./
COPY ayaan ./ayaan
RUN pip install --no-cache-dir .
USER ayaan
EXPOSE 8080
CMD ["uvicorn", "ayaan.api:app", "--host", "0.0.0.0", "--port", "8080"]
