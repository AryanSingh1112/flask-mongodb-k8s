# Design, DNS, Resources & Part-1 Cookie Point

## 0. Cookie point (Part 1): why use a Python virtual environment?
* **Dependency isolation** - each project gets its own packages/versions; project A on Flask 2.0 and project B on Flask 3.0 can coexist.
* **Reproducibility** - `pip freeze`/`requirements.txt` describes exactly what the app needs; "works on my machine" becomes rarer.
* **No system pollution** - you never `sudo pip install` into the OS Python (which OS tools rely on; PEP 668 forbids it on modern distros).
* **No admin rights needed**, trivial to delete and recreate (`rm -rf venv`).
* **Clean dependency audit** - only what the project uses is installed, making security scanning and Docker image builds accurate.
Containers give a similar isolation at OS level, but a venv is still the right tool for local development and for CI.

## 1. Architecture

```
                 laptop ──NodePort 30080──┐
                                          ▼
   ┌──────────────── namespace: flask-app ─────────────────────────────┐
   │  Service flask-service (NodePort :80)                              │
   │        │ load-balances (kube-proxy)                                │
   │   ┌────┴─────┐  HPA 2..5 pods @70% CPU                             │
   │   │ flask-app│×2-5   env: MONGO_HOST=mongodb, user/pass(Secret)    │
   │   └────┬─────┘                                                     │
   │        ▼  DNS: mongodb.flask-app.svc.cluster.local:27017           │
   │  Service mongodb (ClusterIP – cluster-internal only)               │
   │        ▼                                                           │
   │  StatefulSet mongodb-0 (--auth) ──► PVC ──► PV hostPath /data/...  │
   └────────────────────────────────────────────────────────────────────┘
```

## 2. How DNS resolution works for inter-pod communication
1. Every cluster runs **CoreDNS** (Deployment in `kube-system`) exposed by the `kube-dns` Service (e.g. `10.96.0.10`).
2. The kubelet writes each pod's `/etc/resolv.conf` with that IP as `nameserver`, plus search domains:
   `search flask-app.svc.cluster.local svc.cluster.local cluster.local` and `options ndots:5`.
3. CoreDNS watches the API server and creates a record for every Service:
   `<service>.<namespace>.svc.cluster.local → ClusterIP`.
4. When Flask connects to host **`mongodb`**, the resolver sees fewer than 5 dots, so it tries the search list in order:
   `mongodb.flask-app.svc.cluster.local` → **match** → returns the Service ClusterIP.
5. The packet to the ClusterIP is intercepted by **kube-proxy** (iptables/IPVS rules on the node) and DNAT-ed to a healthy pod IP backing the Service (only pods that pass readiness are endpoints).
6. **Headless Service** (`clusterIP: None`, `mongodb-headless`): DNS returns pod IPs directly, and a StatefulSet pod gets a stable name `mongodb-0.mongodb-headless.flask-app.svc.cluster.local`, which survives rescheduling (the IP may change, the name doesn't).

**How Flask is configured:** `MONGO_HOST=mongodb` (ConfigMap) + credentials (Secret) → URI
`mongodb://flaskuser:***@mongodb:27017/flask_db?authSource=flask_db`.
Short name works within the same namespace; across namespaces use `mongodb.flask-app` or the FQDN.
Pod IPs are ephemeral, so **never hard-code them**.

Verify yourself:
```bash
kubectl exec -n flask-app deploy/flask-app -- python -c "import socket;print(socket.gethostbyname('mongodb'))"
kubectl run dns-test -n flask-app --rm -it --image=busybox:1.36 --restart=Never -- nslookup mongodb
kubectl run dns-test -n flask-app --rm -it --image=busybox:1.36 --restart=Never -- nslookup mongodb-0.mongodb-headless
```

## 3. Resource requests and limits
| | Meaning | Used by | If exceeded |
|---|---|---|---|
| **request** | Guaranteed minimum, a reservation | **Scheduler** (pod only lands on a node with that much unreserved) and CPU shares; **HPA** uses it as the 100% baseline | n/a |
| **limit** | Hard ceiling | kubelet / container runtime (cgroups) | **CPU → throttled** (slowed, not killed). **Memory → OOMKilled** |

Configured for both Flask and MongoDB: **request 200m CPU / 250M memory, limit 500m CPU / 500M memory.**

Why it matters: prevents noisy neighbours, allows sensible bin-packing, protects the node from a leak, makes the HPA possible (no request ⇒ no utilisation %), and sets the QoS class (here **Burstable**, since requests < limits; equal requests and limits would give *Guaranteed*, the last to be evicted - a good choice for a production DB).
Caveat applied here: MongoDB caches aggressively, so `--wiredTigerCacheSizeGB 0.25` keeps it inside the 500M limit.

## 4. Design choices and alternatives considered
| Area | Chosen | Alternatives & why not |
|---|---|---|
| Image / server | `python:3.12-slim`, **gunicorn**, non-root | `alpine` (musl wheels issues, slower builds); Flask dev server (not production-safe, single-threaded) |
| Dependency versions | Flask 3.0.3 / pymongo 4.8 / **mongo:7.0** | Assignment pins (Flask 2.0.2, pymongo 3.12) are 2021-era; pymongo 3.12 officially supports MongoDB ≤5.0, so pairing it with `mongo:latest` (8.x) is risky. Modern pinned set is maintained and compatible. API surface used by `app.py` is identical. |
| Mongo auth | Root user (init only) + **least-privilege app user** via init script | App using root credentials (blast radius); keyfile/x509 (only needed for replica sets) |
| Mongo workload | **StatefulSet**, 1 replica | Deployment (no stable identity/storage semantics). Replica set (3 pods) gives HA but needs rs.initiate, keyfile and more RAM; out of scope for a single-node Minikube, documented as next step. MongoDB cannot be scaled just by raising `replicas`. |
| Storage | Static **PV (hostPath, Retain) + PVC**, as required | `volumeClaimTemplates` + default StorageClass (preferred in real clusters: per-replica dynamic PVs; not used because the brief explicitly asks for PV+PVC). hostPath is node-local and Minikube-only. |
| Mongo Service | **ClusterIP** (+ headless for StatefulSet identity) | NodePort/LoadBalancer would expose the DB outside the cluster. NetworkPolicy additionally restricts callers to Flask pods. |
| Flask Service | **NodePort** | `LoadBalancer`+`minikube tunnel` (works, but extra step); Ingress (needs addon + host config; overkill here); `port-forward` (single pod, no LB, dev only) |
| Autoscaling | HPA v2 on CPU 70%, 2-5, fast scale-up/120 s scale-down | Memory-based (Flask isn't memory-bound), KEDA (heavier dependency) |
| Probes | Liveness `/healthz` (no DB), readiness `/readyz` (DB ping) | Single probe on `/` would restart pods during a DB outage (cascading failure) |
| MongoDB probes | **TCP-socket** probes on port 27017 (startup/readiness/liveness) | An exec probe running `mongosh ... ping` timed out under the 500m CPU limit during load testing and caused repeated container restarts (see TESTING.md, issue 5). A TCP check is almost free; the trade-off is that it only proves the port is open, not that authentication or queries work. |
| Image delivery | Build locally, `minikube image load`; registry push documented | Pushing to Docker Hub (needs an account and network; steps are in the README). `imagePullPolicy: IfNotPresent` makes the loaded image usable. |
| Load testing | Temporary CPU-bound `/load` endpoint + in-cluster busybox loops | `hey`/`ab` from the laptop (extra tooling; traffic must cross the Minikube tunnel). `/load` is test-only and should be removed or protected in production. |
| Config | ConfigMap (non-secret) + Secret (credentials) | Baking into image (not rotatable, leaks secrets) |
| Packaging | Plain YAML + `kustomization.yaml` for image override | Helm (more powerful; heavier than needed for 11 manifests) |
| Hardening | Non-root, read-only rootfs, drop caps, seccomp, PDB, anti-affinity | – |

### Lessons from testing (what I would change)
* **Readiness coupled to the database:** `/readyz` pings MongoDB, which stops clients getting errors from a pod that cannot reach the DB, but when MongoDB restarted under load every Flask pod went `0/1` Ready and the Service had no endpoints. In production I would keep readiness DB-independent, or degrade gracefully, and use a replica set so the DB is not a single point of failure.
* **Resource sizing:** with only 2 CPUs on Minikube the load generators, Flask and MongoDB compete for the same cores, so MongoDB hit its 500m CPU limit. On a real cluster, MongoDB would get `requests == limits` (Guaranteed QoS) and its own node pool.
* **HPA flapping:** load between the capacity of N and N+1 pods makes replicas oscillate; the 120 s scale-down stabilisation window damps this.

### Known limitations / production next steps
Secrets in Git → Sealed Secrets / External Secrets / Vault · MongoDB replica set or managed DB (Atlas) · backups (`mongodump` CronJob / volume snapshots) · TLS for Mongo connections · Ingress + TLS · remove demo passwords · CI to build, scan (Trivy) and sign images · requests/limits tuned from real load data.