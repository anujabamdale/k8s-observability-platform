"""
A small "order service" API instrumented with Prometheus metrics.

Deliberately includes:
- An endpoint with variable, sometimes-slow latency (to produce a realistic
  latency histogram instead of a flat line).
- An endpoint with a configurable failure rate (to produce realistic error-rate
  graphs and something for alerting rules to fire on).
- A CPU-burn endpoint used by the load test to trigger the HPA (Horizontal
  Pod Autoscaler) so autoscaling can be demonstrated, not just configured.
"""
import os
import random
import time
import uuid

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

app = FastAPI(title="Order Service", version="1.0.0")

# --- Metrics -----------------------------------------------------------
REQUEST_COUNT = Counter(
    "order_service_requests_total",
    "Total requests received",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "order_service_request_latency_seconds",
    "Request latency in seconds",
    ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
IN_PROGRESS = Gauge(
    "order_service_in_progress_requests",
    "Requests currently being processed",
)
ORDERS_CREATED = Counter(
    "order_service_orders_created_total",
    "Total number of orders successfully created",
)
ORDER_FAILURES = Counter(
    "order_service_order_failures_total",
    "Total number of order creation failures",
    ["reason"],
)

FAILURE_RATE = float(os.environ.get("SIMULATED_FAILURE_RATE", "0.05"))


@app.middleware("http")
async def track_metrics(request, call_next):
    endpoint = request.url.path
    IN_PROGRESS.inc()
    start = time.time()
    try:
        response = await call_next(request)
        REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
        return response
    finally:
        REQUEST_LATENCY.labels(endpoint).observe(time.time() - start)
        IN_PROGRESS.dec()


@app.get("/health")
def health():
    """Liveness probe — cheap, no dependencies."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness probe — would check DB/downstream deps in a real service."""
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/orders")
def create_order():
    """
    Simulates variable processing time (order validation, inventory check,
    payment call) and a realistic failure rate — gives Grafana/Prometheus
    something interesting to graph instead of a flat 0ms/0% line.
    """
    processing_time = random.choice([0.02, 0.05, 0.08, 0.15, 0.4, 0.05, 0.03])
    time.sleep(processing_time)

    if random.random() < FAILURE_RATE:
        ORDER_FAILURES.labels(reason="payment_declined").inc()
        raise HTTPException(status_code=502, detail="Payment provider error")

    ORDERS_CREATED.inc()
    return {"order_id": str(uuid.uuid4()), "status": "created", "processing_time_s": processing_time}


@app.get("/cpu-intensive")
def cpu_intensive(iterations: int = 2_000_000):
    """
    Deliberately burns CPU so the load test can push a pod's CPU usage high
    enough to trigger the HorizontalPodAutoscaler — this is what turns "I
    configured an HPA" into "I demonstrated autoscaling under real load."
    """
    total = 0
    for i in range(iterations):
        total += i % 7
    return {"result": total}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
