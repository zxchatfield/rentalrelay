FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py password.py stripe_client.py manager_api_delivery.py muse_preflight.py muse_queue.py autopilot_worker.py facebook_page_scheduler.py scheduled_workers.py customer_notifications.py inquiry_desk.py research_leads.py database.py ./
COPY templates ./templates
COPY static ./static
COPY DETROIT_INVENTORY ./DETROIT_INVENTORY
RUN useradd -m -u 10001 rr && mkdir -p /data && chown rr:rr /data
USER 10001
EXPOSE 8000
# Use $PORT when the host assigns one (free-tier hosts do); default 8000 otherwise.
# Changed 2026-11-25 for $0 hosting readiness; behaviour in Docker Compose unchanged.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
