FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN rm -rf .venv logs xiaonuan.db chaos_test.db loadtest.db .secrets.json static/voice
ENV XIAONUAN_HOST=0.0.0.0 XIAONUAN_PORT=8090
EXPOSE 8090
CMD ["python", "server.py"]