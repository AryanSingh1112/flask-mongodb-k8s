# Testing Scenarios and Results

**Environment:** Windows 11 Home, Docker Desktop, Minikube v1.39.0 (docker driver, 2 CPUs, 2500 MB), kubectl, metrics-server addon.
All numbers below were observed on my machine; screenshots are in `/screenshots`.

## 1. Application and database interaction
| Test | Result | Evidence |
|---|---|---|
| `GET /` | Returned "Welcome to the Flask app! The current time is: ..." | `02-app-and-db-working.png` |
| `POST /data` with `{"sampleKey":"sampleValue"}` | `Data inserted` (HTTP 201) | `02-app-and-db-working.png` |
| `GET /data` | Returned the stored documents | `02-app-and-db-working.png` |
| All resources deployed | `mongodb-0`, Flask pods Running, PVC `Bound`, `mongodb` = ClusterIP, `flask-service` = NodePort | `01-deployed-resources.png` |

## 2. Security, persistence, DNS, resources
> **TO COMPLETE before submitting:** run each command, paste the real result, add the screenshot.

| Test | Command | Expected | My result | Evidence |
|---|---|---|---|---|
| Unauthenticated access rejected | `kubectl exec -n flask-app mongodb-0 -- mongosh --quiet --eval "db.getSiblingDB('flask_db').data.find().toArray()"` | "requires authentication" error | _paste_ | `09-auth-rejected.png` |
| Data persists across pod deletion | delete `mongodb-0`, wait, `GET /data` | record still present | _paste_ | `10-persistence.png` |
| DNS resolution | `nslookup mongodb` from a pod | resolves to the `mongodb` ClusterIP | _paste_ | `11-dns.png` |
| Requests/limits | `kubectl describe pod ...` | 200m/250M requests, 500m/500M limits | _paste_ | `12-resources.png` |

## 3. Autoscaling and simulated high traffic
**Method.** Load came from inside the cluster. First, busybox pods looping `wget` against `/` and `/data`. Later, a test-only `/load` endpoint (a CPU-bound loop, added to `app.py` for this purpose and not part of the required API) was hit with PowerShell background jobs. HPA: CPU target 70%, min 2, max 5, measured against the 200m CPU request.

| Stage | Observed | Replicas | Evidence |
|---|---|---|---|
| Idle baseline | 3-4% / 70% | 2 | `03-hpa-idle-baseline.png` |
| First load (4 generators) | about 52% (below target, correctly no scaling) | 2 | (observed, not captured) |
| Increased load | 80% -> HPA scaled up about 14 s later | 2 -> 3 | `04-hpa-first-test-scale-up-down.png` |
| 3 pods share load | 34-36% | 3 | same |
| Load fell | 52% | 3 -> 2 | same |
| Load oscillating | 87-90% again | 2 -> 3 | same |
| Heavy load via `/load` | 54% -> 77% -> **242%** | 2 -> **4** | `05-hpa-scaling-to-4.png` |
| Peak | 206% | **5 (maximum)** | `06-hpa-max-5-replicas.png` |
| Load stopped | 2% / 70% | back to **2** | `08-hpa-scale-down-to-2.png` |

Scale-up followed the HPA formula `ceil(replicas x current% / target%)`; scale-down waited for the 120 s stabilisation window configured in `08-flask-hpa.yaml`.

## 4. Issues encountered
1. **Docker Desktop would not start** ("Virtual Machine Platform is not enabled"). Windows Home has no Hyper-V; fixed by enabling the Virtual Machine Platform feature in an administrator PowerShell (`wsl --install --no-distribution`) and doing a full restart.
2. **`kubectl` "connection refused localhost:8080"**: no cluster/context existed because Minikube was not started. Fixed with `minikube start`.
3. **Load too light at first**: 4 generators only reached ~52%, below the 70% target, so the HPA correctly did nothing. Fixed by adding more load.
4. **Replica oscillation (2 <-> 3)**: demand sat between the capacity of 2 and 3 pods; expected from the HPA formula, damped by the scale-down stabilisation window.
5. **MongoDB container restarted repeatedly under load** (restart count rose to 11). `kubectl describe` showed the exec probe `mongosh ... ping` timing out after 5 s, and `kubectl top` showed `mongodb-0` using ~434m of its 500m CPU limit on a 2-CPU node shared with the load generators (`07-mongodb-probe-timeout-issue.png`). During peak load all Flask pods showed `0/1` Ready, because Flask readiness (`/readyz`) depends on MongoDB. Most likely cause: CPU throttling plus a heavy `mongosh` probe. **Fix:** replaced the exec probes with lightweight TCP-socket probes (see `k8s/05-mongodb-statefulset.yaml`).
   **Verification of the fix:** _[UPDATE: after applying, state the restart count you observed, e.g. "0 restarts after N minutes". Do not claim it is fixed unless you confirmed this.]_
   **Trade-off noted:** coupling Flask readiness to MongoDB protects clients from failing requests, but a database outage removes all Flask pods from the Service.

## 5. Limitations
Standalone MongoDB (no replica set), hostPath storage (Minikube only), demo credentials in a Secret manifest, tested on a single-node Minikube with only 2 CPUs, so the load generators and the application compete for the same CPU.