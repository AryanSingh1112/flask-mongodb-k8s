FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy requirements first: this layer is cached until dependencies change.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

# Never run as root inside the container.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser
USER 10001

EXPOSE 5000

# Production WSGI server (Flask's built-in server is for development only).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]