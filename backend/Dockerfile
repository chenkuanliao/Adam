FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 adam && useradd --uid 10001 --gid adam --create-home adam
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY adam_server ./adam_server
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN mkdir -p /data && chown -R adam:adam /data /app
USER adam
EXPOSE 8000
CMD ["uvicorn", "adam_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
