FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY research_bridge/ ./research_bridge/
COPY server.py mcp_http.py ./
RUN pip install --no-cache-dir .
RUN useradd -r -u 10001 app && mkdir -p /app/data && chown app:app /app/data
USER app
ENV RESEARCH_DB=/app/data/observations.sqlite3
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]