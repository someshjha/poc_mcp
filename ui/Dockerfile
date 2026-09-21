FROM python:3.12-slim
WORKDIR /app
COPY ui/requirements.txt ./ui/requirements.txt
RUN pip install --no-cache-dir -r ui/requirements.txt
COPY ui ./ui
EXPOSE 5000
CMD ["uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "5000"]
