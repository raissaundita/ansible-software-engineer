# Personal Finance Tracker API: Enterprise Software Engineer Bootcamp

Backend project built during the **Enterprise Software Engineer Bootcamp ("From Logic to Automation")** by PT Multipolar Technology x ITS Mathematics Department.

The case study is a user named **Ferdi**, who wants to record his cashflow, understand his spending habits, and get warned when his spending in the current month is getting out of hand. The project covers the whole path from writing the API, to containerizing it, deploying it on OpenShift, and automating VM setup with Ansible.

## Tech Stack

| Area | Tools |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic, Beanie (async ODM), pandas, openpyxl |
| Database | MongoDB (Atlas) |
| Container | Podman, Containerfile |
| Orchestration | OpenShift (Red Hat Developer Sandbox): Deployment, Service, Route, Secret, ConfigMap |
| Automation | Ansible, Ansible Automation Platform (AAP), OpenShift Virtualization (Fedora VM) |
| Dev environment | OpenShift DevSpaces (VS Code in the browser), Git/GitHub |

## Architecture

```
          Route (public URL)                 Route (public URL)
                 |                                  |
         Service (8000)                      Service (8001)
                 |                                  |
   Deployment: transaction-service     Deployment: profiling-service
      (label bootcamp: its-mlpt)        (label bootcamp: its-profiling)
                 \                                  /
                  \--------- Secret + ConfigMap ---/
                                  |
                            MongoDB Atlas
```

Both services read database credentials from an OpenShift **Secret** (`MONGO_USER`, `MONGO_PASSWORD`, `MONGO_HOST`) and **ConfigMap** (`MONGO_DB_NAME`), so no credential is baked into the image. Each service uses a different pod label so its Service only routes traffic to its own pods.

## Features

### Transaction Service (port 8000)

| Method | Endpoint | What it does |
|---|---|---|
| POST | `/transaction/add` | Add a transaction. Input is validated first (integer amount of at least 1, known payment method, type must be `pemasukan` or `pembelian`, optional `YYYY-MM-DD` date) and errors are returned as readable Indonesian messages |
| GET | `/transaction` | List transactions filtered by `start_date` and `end_date` |
| PATCH | `/transaction/{id}` | Partially update a transaction (only the fields that are sent change) |
| DELETE | `/transaction/{id}` | Delete a transaction |
| GET | `/transaction/summary` | Monthly total and count per type (income vs purchase), using a MongoDB aggregation pipeline |
| GET | `/transaction/summary/behavior` | Classifies a month as **Reckless Spender**, **Cukup Terkendali**, or **Big Saver** based on savings rate |
| POST | `/transaction/import-excel/preview` | Preview an Excel file (detected columns, first rows) before importing |
| POST | `/transaction/import-excel` | Bulk import from Excel. Column names are matched flexibly (e.g. `jumlah` is read as `amount`), and the transaction type is inferred from the sign of the amount when no type column exists |

**Savings rate instead of fixed thresholds.** The behavior label is based on `savings_rate = (income - purchase) / income`, not on a fixed rupiah limit. A fixed limit does not fit users with different incomes, and Ferdi's own monthly income and spending vary a lot. Below 0 means spending exceeds income (Reckless Spender), at or above `ambang_big_saver` (default 20%, optional parameter) is Big Saver, anything in between is Cukup Terkendali. The response also returns the savings rate itself so the label is explainable.

### Profiling Service (port 8001)

`GET /profiling/alert` warns Ferdi when this month's spending goes beyond his usual pattern. An optional `tanggal` parameter (`YYYY-MM-DD`) checks the position as of another date.

- **Baseline (x):** average total monthly spending across previous months
- **Current spending (y):** spending so far this month
- **Status** from `y / x`: `aman` (< 50%), `waspada_ringan` (50-80%), `waspada` (80-100%), `lewat_batas` (>= 100%), or `belum_ada_baseline` when there is no history yet
- **Extra insights:** comparison with last month, average spending per day, last recorded transaction date, and the top 3 largest transactions this month
- Alert messages vary per status so the output does not feel repetitive

## Ansible Automation

`ansible/setup-vm-dev.yaml` prepares a Fedora VM and is launched from an AAP Job Template, with values passed as extra variables.

- **Pre-validation with `assert`:** repository URL, registry host, registry credentials, OpenShift API URL, and the token format (`sha256~`) are checked first, so the playbook fails early with a clear message
- **Exact package versions:** `git`, `podman`, and `python3` are pinned (no `latest`) so every run gives the same result
- Installs the `oc` CLI, verifies the installed versions, clones a git repository, and logs in to the container registry and the OpenShift cluster
- **`no_log: true`** on tasks that handle passwords and tokens, so secrets never appear in the job output

## Repository Structure

```
.
├── ansible/
│   └── setup-vm-dev.yaml        # VM setup playbook (run through AAP)
├── manifest/                    # OpenShift manifests
│   ├── deployment.yaml          # transaction service Deployment
│   ├── service.yaml             # transaction service Service
│   ├── profildeploy.yaml        # profiling service Deployment
│   └── profilservice.yaml       # profiling service Service
├── transaction/                 # Transaction Service (FastAPI)
│   ├── main.py
│   ├── Containerfile
│   └── requirements.txt
└── profiling/                   # Profiling Service (FastAPI)
    ├── main.py
    ├── profiling.py             # baseline, insight, and status calculation
    ├── Containerfile
    └── requirements.txt
```

## Run Locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r transaction/requirements.txt

# Create a .env file in the repository root (it is git-ignored):
#   MONGO_USER=...
#   MONGO_PASSWORD=...
#   MONGO_HOST=...
#   MONGO_DB_NAME=...

fastapi dev transaction/main.py        # docs at http://127.0.0.1:8000/docs
```

For the profiling service, install `profiling/requirements.txt` and run `fastapi dev profiling/main.py --port 8001`.

## Build and Deploy

```bash
# Build and test the image locally (the .env file is not part of the image)
cd transaction
podman build -t transaction:v.1 .
podman run -p 8000:8000 --env-file ../.env transaction:v.1

# Log in to the OpenShift internal registry, then tag and push
podman login -u "$(oc whoami)" -p "$(oc whoami --show-token)" <registry-host>
podman tag transaction:v.1 <registry-host>/<namespace>/transaction:v.1
podman push <registry-host>/<namespace>/transaction:v.1

# Deploy (Secret mongo-secret and ConfigMap mongo-configmaps must exist first)
oc apply -f manifest/deployment.yaml
oc apply -f manifest/service.yaml
oc expose service/my-container       # creates a Route with a public URL
```

The profiling service follows the same steps with `manifest/profildeploy.yaml` and `manifest/profilservice.yaml`.

## Challenges and Lessons Learned

- `podman build` failed with a UID/GID error on `python:3.11-slim` for one service; switching the base image to `python:3.11` fixed it
- The `.env` file is not copied into the image, so local container runs need `--env-file`, and in the cluster the credentials come from the Secret and ConfigMap instead
- Every application needs its own pod labels, otherwise a Service can send traffic to the wrong pods
- A fixed-nominal spending threshold gave the same label every month for Ferdi's data, which led to the switch to savings rate

## Status

- Transaction Service and Profiling Service: **done**, deployed on OpenShift
- Ansible VM setup playbook: **done**
- Self-hosted MongoDB on the VM to replace Atlas: **in progress**. MongoDB 7.0 is running as a Podman container on the VM; connecting it to the OpenShift services is not finished yet

## Author

Raissa Undita Estiningtyas, Mathematics, Institut Teknologi Sepuluh Nopember (ITS)