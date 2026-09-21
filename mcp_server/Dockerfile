FROM python:3.12-slim
WORKDIR /app
COPY scripts/requirements.txt ./scripts/requirements.txt
RUN pip install --no-cache-dir -r scripts/requirements.txt
COPY mcp_server ./mcp_server
EXPOSE 8000
CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8000"]
