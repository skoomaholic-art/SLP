# Web-only entrypoint for Google Cloud Run. The Telegram bot stays on main.py.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 SLP_DB_PATH=/tmp/slp/slp.db
WORKDIR /app
COPY requirements.txt requirements-cloudrun.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements-cloudrun.txt
COPY agents/ ./agents/
COPY parsers/ ./parsers/
COPY services/ ./services/
COPY storage/ ./storage/
COPY models.py config.py cloudrun_web.py ./
COPY cloudrun_ui/ ./cloudrun_ui/
RUN mkdir -p /tmp/slp && useradd --uid 10001 --create-home appuser && chown -R appuser:appuser /tmp/slp
USER appuser
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn cloudrun_web:app --host 0.0.0.0 --port $PORT --workers 1"]
