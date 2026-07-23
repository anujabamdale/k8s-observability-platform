"""
Load test for the order service.

Two user classes on purpose:
- OrderServiceUser hits realistic endpoints (/orders, mixed with occasional
  reads) to produce a normal traffic-shaped load profile.
- CPUStressUser hammers /cpu-intensive specifically — this is the traffic
  pattern needed to actually push a pod's CPU usage over the HPA's 70%
  threshold and observe a real scale-up event, not just a configured HPA
  that's never been exercised.

Run: locust -f locustfile.py --host http://localhost:8000
Then open http://localhost:8089 to start a run and watch RPS/latency live.

For headless / CI-style runs:
locust -f locustfile.py --host http://localhost:8000 --headless \
  -u 50 -r 5 --run-time 3m
"""
from locust import HttpUser, task, between


class OrderServiceUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(5)
    def create_order(self):
        self.client.post("/orders")

    @task(1)
    def health_check(self):
        self.client.get("/health")


class CPUStressUser(HttpUser):
    """
    Weight this class up (e.g. spawn more of these) when you specifically
    want to demonstrate HPA scale-up under CPU pressure.
    """
    wait_time = between(0.01, 0.1)

    @task
    def burn_cpu(self):
        self.client.get("/cpu-intensive?iterations=3000000")
