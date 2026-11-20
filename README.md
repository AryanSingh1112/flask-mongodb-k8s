# Flask + MongoDB on Kubernetes (Minikube)

Flask API (`/`, `/data`) backed by an **authenticated** MongoDB StatefulSet with persistent storage, HPA (2-5 pods at 70% CPU), cluster-internal DB access, and resource requests/limits (0.2 CPU / 250M requests, 0.5 CPU / 500M limits).

## Layout
```
app/          app.py (Mongo auth), requirements.txt, Dockerfile
k8s/          all manifests + kustomization.yaml
docs/         DESIGN.md (DNS, requests/limits, design choices), TESTING.md (tests + results)
screenshots/  evidence from my test run
```

## Notes
* Credentials in `k8s/01-secret.yaml` are **demo values**; use a secrets manager in real environments.
* `app.py` also contains a **test-only `/load` endpoint** (CPU loop) used to generate autoscaling load. It is not part of the required API and should be removed or protected in production.
* Tested on Windows 11 + Docker Desktop + Minikube (docker driver).

## 1. Build the image
```powershell
docker build -t flask-mongo-app:1.0.0 app/
```
**Local use (what I did):** load it straight into Minikube, no registry needed.
```powershell
minikube image load flask-mongo-app:1.0.0
```
**Push to a registry (instructions; I did not push in my test run):**
```powershell
docker login
docker tag flask-mongo-app:1.0.0 <your-dockerhub-username>/flask-mongo-app:1.0.0
docker push <your-dockerhub-username>/flask-mongo-app:1.0.0
```
Then set `image: <your-dockerhub-username>/flask-mongo-app:1.0.0` in `k8s/06-flask-deployment.yaml`.

## 2. Start Minikube
```powershell
minikube start --driver=docker --cpus=2 --memory=2500
minikube addons enable metrics-server      # required for the HPA
```

## 3. Deploy
```powershell
kubectl apply -k k8s/
kubectl rollout status statefulset/mongodb -n flask-app --timeout=180s
kubectl rollout status deployment/flask-app -n flask-app --timeout=180s
kubectl get all,pvc,hpa -n flask-app
```

## 4. Access and verify
```powershell
minikube service flask-service -n flask-app --url     # keep this window open
$URL = "<printed url>"
Invoke-RestMethod $URL/
Invoke-RestMethod -Method Post -Uri "$URL/data" -ContentType "application/json" -Body '{"sampleKey":"sampleValue"}'
Invoke-RestMethod $URL/data
```
Authentication check (must fail):
```powershell
kubectl exec -n flask-app mongodb-0 -- mongosh --quiet --eval "db.getSiblingDB('flask_db').data.find().toArray()"
```
Full test plan and results: [docs/TESTING.md](docs/TESTING.md).

## 5. Autoscaling demo
```powershell
kubectl get hpa -n flask-app -w          # terminal 1
1..8 | ForEach-Object { kubectl run "load-gen-$_" -n flask-app --image=busybox:1.36 --restart=Never --labels=role=load-gen -- /bin/sh -c "while true; do wget -q -O- http://flask-service/ > /dev/null; done" }
kubectl delete pod -n flask-app -l role=load-gen --wait=false     # stop the load
```

## 6. Clean up
```powershell
kubectl delete -k k8s/
minikube ssh -- sudo rm -rf /data/flask-mongodb     # also wipes DB data
minikube stop
```

## Required explanations
* DNS resolution in the cluster, requests/limits, design choices and alternatives: [docs/DESIGN.md](docs/DESIGN.md)
* Testing scenarios, results and issues: [docs/TESTING.md](docs/TESTING.md)