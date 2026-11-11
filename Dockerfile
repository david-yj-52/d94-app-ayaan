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
# Cursor is installed with a throwaway HOME outside /home/ayaan, because compose mounts /home/ayaan
RUN mkdir -p /opt/cursor && chown ayaan:ayaan /opt/cursor
USER ayaan
RUN HOME=/opt/cursor sh -c 'curl https://cursor.com/install -fsS | bash'
USER root
# Docs name the binary `agent`; link both names, failing the build if either target is missing
RUN for b in agent cursor-agent; do \
        t="$(readlink -f /opt/cursor/.local/bin/$b)" && test -x "$t" && ln -s "$t" /usr/local/bin/$b || exit 1; \
    done \
    && agent --version && cursor-agent --version
ENV HOME=/home/ayaan
WORKDIR /app
COPY pyproject.toml ./
COPY ayaan ./ayaan
RUN pip install --no-cache-dir .
COPY docker/entrypoint.sh /usr/local/bin/ayaan-entrypoint
# USER stays root only for the entrypoint: it fixes bind-mount ownership, then drops to uid 10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/ayaan-entrypoint"]
CMD ["uvicorn", "ayaan.api:app", "--host", "0.0.0.0", "--port", "8080"]
