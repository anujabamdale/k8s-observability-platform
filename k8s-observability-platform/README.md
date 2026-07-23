# Kubernetes Observability & Autoscaling Platform

A FastAPI service deployed on Kubernetes with full observability (Prometheus + Grafana),
autoscaling under real load (HPA), and reliability guarantees (PDB, health probes) —
plus a load test designed specifically to trigger the autoscaler, not just configure it.

## Architecture

```
                     ┌───────────────────────────────┐
   locustfile.py  →  │  order-service (2-8 pods)      │
   (load test)       │  FastAPI + /metrics endpoint   │
                     └───────────────────────────────┘
                          │scraped by         │HPA watches
                          ▼                    ▼
                     ┌──────────┐        ┌─────────────┐
                     │Prometheus │◄──────►│ CPU metrics  │
                     │+ alerts  │        │ → scale 2-8  │
                     └──────────┘        └─────────────┘
                          │
                          ▼
                     ┌──────────┐
                     │ Grafana   │  (dashboards: RPS, error %, p50/p95/p99, in-flight)
                     └──────────┘
```

## Why these design choices (interview talking points)

- **HPA scales on CPU%, with resource `requests`/`limits` set explicitly** —
  CPU-percentage autoscaling is meaningless without a request value as the
  denominator. This is a common misconfiguration in real clusters: an HPA
  target with no resource requests set silently never triggers.
- **Asymmetric scale-up/scale-down behavior** — scale up fast (30s stabilization,
  +100%/30s) to absorb traffic spikes, but scale down slowly (120s stabilization,
  -25%/60s) to avoid flapping — repeatedly scaling down then back up, which
  wastes resources on pod churn and can drop requests during the transition.
- **`/health` vs `/ready` are separate endpoints** — liveness shouldn't depend on
  downstream dependencies. If `/ready` checked a database and the DB blipped,
  a liveness probe reusing that same check would kill and restart a perfectly
  healthy process, adding a restart storm on top of an already-degraded dependency.
- **PodDisruptionBudget (`minAvailable: 1`)** — without this, a node drain during
  a cluster upgrade could evict all replicas at once. This turns "I have 2 replicas"
  into an actual availability guarantee during maintenance operations.
- **Alerting on p95 latency and error *rate*, not averages or raw counts** —
  average latency hides a slow tail that still affects real users; an absolute
  error count either never fires (low traffic) or always fires (high traffic).
  A ratio-based, percentile-based approach scales correctly with traffic volume.
- **A dedicated `/cpu-intensive` endpoint + `CPUStressUser` load-test class** —
  built specifically so the HPA can be exercised and observed scaling live,
  rather than just existing as unverified YAML.

## Run it locally (docker-compose)

```bash
docker compose up --build
```
- App: http://localhost:8000/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous viewer access enabled; admin/admin for editing)
  — the "Order Service Overview" dashboard is auto-provisioned.

Generate traffic:
```bash
pip install locust
locust -f loadtest/locustfile.py --host http://localhost:8000
# open http://localhost:8089, set users=50, spawn rate=5, and start
```
Watch the Grafana dashboard update live as load ramps up.

## Deploy to a real Kubernetes cluster (minikube / kind / EKS / GKE)

```bash
# Build and load the image (for minikube):
docker build -t order-service:latest ./app
minikube image load order-service:latest

# Apply manifests in order:
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/02-deployment.yaml
kubectl apply -f k8s/03-hpa.yaml
kubectl apply -f k8s/04-pdb.yaml

# Watch pods and HPA:
kubectl get pods -n order-service -w
kubectl get hpa -n order-service -w
```

Port-forward and load test against the real cluster to watch it scale:
```bash
kubectl port-forward -n order-service svc/order-service 8000:80
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 100 -r 10 --run-time 5m
# in another terminal: kubectl get hpa -n order-service -w
```

You should see `REPLICAS` climb from 2 toward 8 as CPU utilization crosses 70%,
then slowly settle back down ~2 minutes after load stops.

## Metrics exposed (`/metrics`)

| Metric | Type | Purpose |
|---|---|---|
| `order_service_requests_total` | Counter | RPS, error rate, per-endpoint breakdown |
| `order_service_request_latency_seconds` | Histogram | p50/p95/p99 latency |
| `order_service_in_progress_requests` | Gauge | Current concurrency |
| `order_service_orders_created_total` | Counter | Business metric, not just infra |
| `order_service_order_failures_total` | Counter | Failure reason breakdown |

## What I'd add with more time

- Real `kube-prometheus-stack` Helm install + `ServiceMonitor` CRD instead of pod annotations
- Alertmanager wired to Slack/PagerDuty for the alert rules already defined
- A chaos experiment (e.g., Chaos Mesh pod-kill) to validate the PDB and readiness
  probes actually protect availability, not just that they're configured
- Multi-AZ node affinity/anti-affinity rules so replicas spread across failure domains
