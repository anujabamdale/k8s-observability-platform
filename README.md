# MLOps Churn Prediction Pipeline

An end-to-end MLOps pipeline: training with experiment tracking, model serving via API,
automated drift detection, and a CI/CD pipeline that gates deployment on model quality.

Most portfolio ML projects stop at a notebook that trains a model. This project focuses
on everything *around* the model that makes it operable in production: versioning,
serving, monitoring, and automated retraining triggers.

## Architecture

```
                     ┌─────────────────┐
  generate_data.py → │  train.py        │ → logs params/metrics/model → MLflow
                     │  (quality gate)  │ → saves model.joblib + baseline_stats.json
                     └─────────────────┘
                              │
                              ▼
                     ┌─────────────────┐
                     │  serve.py         │ → FastAPI /predict, /health, /ready
                     │  (Docker)        │
                     └─────────────────┘
                              │
                     new production data
                              ▼
                     ┌─────────────────┐
                     │ monitor_drift.py │ → KS-test + PSI vs. training baseline
                     │                  │ → non-zero exit on drift → triggers retrain
                     └─────────────────┘

  All of the above wired together in .github/workflows/ci-cd.yml
```

## Why these design choices (interview talking points)

- **MLflow over ad-hoc logging** — gives comparable runs, a model registry, and a
  standard artifact format `serve.py` can load without knowing training internals.
- **A hard quality gate in `train.py`** (`--min_roc_auc`) — a model that doesn't clear
  the bar fails the CI build instead of silently deploying a worse model.
- **KS-test + PSI for drift, not just mean/std comparison** — distribution *shape*
  can shift while the mean stays flat (e.g. a spread-out vs. bimodal distribution).
  KS catches that; comparing summary statistics alone would miss it.
- **`/health` vs `/ready` are separate endpoints** — a Kubernetes liveness probe
  shouldn't depend on model-loading logic. If model loading is slow, the process
  is still "alive" — it's just not ready to serve yet. Conflating the two causes
  orchestrators to kill and restart healthy-but-slow-starting pods.
- **`model.joblib` saved alongside MLflow tracking** — the serving container
  doesn't need a live MLflow tracking server at inference time, which would be a
  single point of failure for every prediction request.
- **Synthetic data generation instead of a downloaded dataset** — keeps the repo
  self-contained and license-free, and lets the drift demo be deterministic
  (`generate(..., drift=True)` produces a known, reproducible shift to validate
  the monitor actually works, rather than hoping a real dataset drifts).

## Local setup

```bash
pip install -r requirements.txt
python src/generate_data.py          # creates data/train.csv + two test batches
python src/train.py                  # trains, logs to ./mlruns, saves models/model.joblib
python src/monitor_drift.py --batch_path data/fresh_batch.csv     # should pass
python src/monitor_drift.py --batch_path data/drifted_batch.csv   # should flag drift
pytest tests/ -v
```

Run the API locally:
```bash
uvicorn src.serve:app --reload
curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{
  "tenure_months": 5, "monthly_charge": 95.0, "support_calls": 4,
  "plan_type": "basic", "contract_months": 1
}'
```

## Run the full stack with Docker Compose

```bash
python src/generate_data.py && python src/train.py   # produces models/ artifacts first
docker compose up --build
```
- API: http://localhost:8000/docs (interactive Swagger UI)
- MLflow UI: http://localhost:5000

## CI/CD pipeline (`.github/workflows/ci-cd.yml`)

On every push: generates data → runs unit tests → trains the model (fails the build
if it doesn't clear the ROC-AUC quality gate) → runs API tests against the freshly
trained model → builds the Docker image. A separate scheduled job simulates a nightly
drift check against fresh production-like data.

## What I'd add with more time

- Real feature store instead of CSV snapshots
- Push the Docker image to a real registry (GHCR/ECR) and auto-deploy to a k8s cluster
- Wire the drift-detected exit code to actually trigger a `workflow_dispatch` retraining run
- A/B or shadow-deployment logic for comparing a new model against the current production model
