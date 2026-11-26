# Smoke-task image. Record the resolved image digest in the run manifest before
# comparing production measurements; the tag alone is not an immutable artifact.
FROM python:3.11-slim
ENV PYTHONPATH=/opt/rsiforme
COPY rsiforme /opt/rsiforme/rsiforme
COPY archive-task.toml /opt/rsiforme/archive-task.toml
WORKDIR /workspace
COPY solution.py /workspace/solution.py
RUN mkdir -p /workspace/research
