# Kubernetes + ArgoCD lab — explained

You'll take a tiny Python web app, package it, run it on Kubernetes, and then let ArgoCD deploy it from Git. Every part starts with **the idea**, then each command is broken down so you know what every word does. Nothing here should be typed blindly.

At the end there's a **command cheat sheet** grouped by purpose, so you don't have to search for commands.

---

## The big picture first

Here is the whole journey, from your code to a running, self-managing service:

```
main.py  ──docker build──▶  image  ──kubectl apply──▶  Kubernetes runs 3 copies
                                                        (restarts them, balances traffic)

Later:   git push  ──▶  GitHub  ──ArgoCD watches──▶  Kubernetes updated automatically
```

Three tools, three jobs:

| Tool           | Job                                                                                       | Analogy                                                    |
| -------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **Docker**     | Packages your app + Python + libraries into one sealed box (an _image_)                   | A shipping container: the same box runs anywhere           |
| **Kubernetes** | Runs many copies of that box, restarts dead ones, spreads traffic, rolls out new versions | A port manager who keeps exactly N containers running      |
| **ArgoCD**     | Watches a Git repo and makes Kubernetes match whatever is written there                   | An inspector who keeps the port matching the official plan |

Keep this table in your head. Every command below belongs to one of these three jobs.

---

## The app you're deploying

Open `app/main.py`. It has three endpoints:

| Endpoint  | Returns                               | Why it exists                                                             |
| --------- | ------------------------------------- | ------------------------------------------------------------------------- |
| `/`       | Version (`v1`) and which pod answered | Lets you _see_ load balancing and version changes                         |
| `/health` | `alive`                               | Kubernetes calls this to ask "is the process stuck?" → **liveness**       |
| `/ready`  | `ready`, or 503 if `BREAK_READY=true` | Kubernetes calls this to ask "should I send you traffic?" → **readiness** |

`BREAK_READY` is a switch so you can simulate a broken release later.

---

## Part 0 — Install the tools (30 min)

### The idea

Kubernetes normally runs on a fleet of cloud servers. **kind** ("Kubernetes IN Docker") fakes that fleet on your laptop: each Kubernetes "server" (called a _node_) is actually a Docker container. That's why Docker must be installed and **running** first — kind is built on top of it.

| Tool                 | Install                                                                    | Verify                     | What "verify" proves                                                                 |
| -------------------- | -------------------------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------ |
| Docker Desktop       | docker.com/products/docker-desktop                                         | `docker version`           | Shows both **Client** and **Server**. If Server errors, Docker Desktop isn't running |
| kind                 | Mac: `brew install kind` · Windows: `winget install Kubernetes.kind`       | `kind version`             | kind is on your PATH                                                                 |
| kubectl              | Mac: `brew install kubectl` · Windows: `winget install Kubernetes.kubectl` | `kubectl version --client` | The Kubernetes remote control is installed                                           |
| git + GitHub account | git-scm.com                                                                | `git --version`            | Needed for Part 3                                                                    |

**kubectl** (say "cube-control") is how you talk to Kubernetes. Every kubectl command is a request sent to the cluster's API server: "show me pods", "create this", "delete that".

---

## Part 1 — Docker: from code to image (20 min)

### The idea

Your laptop has Python 3.x and some libraries. A server might have different ones. Docker solves "works on my machine" by packaging **everything the app needs** into an **image**.

- An **image** is a frozen template (like a class).
- A **container** is a running instance of an image (like an object).
- A **Dockerfile** is the recipe for building the image.

### Read the recipe: `app/Dockerfile`, line by line

```dockerfile
FROM python:3.12-slim          # Start from an official image that already has Python 3.12.
                               # "slim" = smaller, fewer extras.
WORKDIR /app                   # Inside the image, work in /app (creates it, like cd).
COPY requirements.txt .        # Copy the dependency list in FIRST...
RUN pip install --no-cache-dir -r requirements.txt   # ...and install it.
COPY main.py .                 # Copy your code in LAST.
EXPOSE 8000                    # Documentation: the app listens on port 8000.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
                               # The command that runs when a container starts.
```

**Why copy `requirements.txt` before `main.py`?** Docker builds in **layers** and caches each one. If you only change `main.py`, Docker reuses the cached "pip install" layer and rebuilds in seconds. If you copied everything at once, every code change would reinstall all libraries. This is a classic interview question.

**Why `--host 0.0.0.0`?** Inside a container, `127.0.0.1` means "only this container". `0.0.0.0` means "accept connections from outside the container" — otherwise nothing could reach your app.

### Build the image

```bash
cd app
docker build -t hello-api:v1 .
```

| Piece             | Meaning                                                                             |
| ----------------- | ----------------------------------------------------------------------------------- |
| `docker build`    | Follow a Dockerfile and produce an image                                            |
| `-t hello-api:v1` | **t**ag it: name `hello-api`, version `v1`. Kubernetes will ask for this exact name |
| `.`               | The _build context_: "the Dockerfile and files are in this folder"                  |

**You'll see:** each Dockerfile step run, ending with `naming to docker.io/library/hello-api:v1`. Run the build a second time — it finishes almost instantly with `CACHED` on each step. That's layer caching.

### Run it

```bash
docker run --rm -p 8000:8000 hello-api:v1
```

| Piece          | Meaning                                                                                                    |
| -------------- | ---------------------------------------------------------------------------------------------------------- |
| `docker run`   | Start a container from an image                                                                            |
| `--rm`         | Delete the container when it stops (keeps things tidy)                                                     |
| `-p 8000:8000` | **p**ort mapping, `laptop:container`. Traffic to your laptop's port 8000 goes to the container's port 8000 |
| `hello-api:v1` | Which image to run                                                                                         |

In a second terminal:

```bash
curl localhost:8000/
```

**You'll see:** `{"version":"v1","pod":"<random id>"}`. The `pod` field shows the container's hostname — Docker gives each container a random one.

Stop it with **Ctrl+C**, then `cd ..` back to the lab root.

### Check yourself

1. What's the difference between an image and a container?
2. Why is `requirements.txt` copied before `main.py`?
3. What does `-p 8000:8000` do, and what breaks without it?

---

## Part 2 — Kubernetes by hand (60–90 min)

### The idea

Running one container with `docker run` is fragile: if it crashes, it stays dead; if traffic grows, there's still one. Kubernetes fixes this. You don't tell it _what to do_ ("start a container"). You tell it **what you want** ("I want 3 copies of hello-api:v1 running") and it works continuously to make reality match. This is called **desired state** or the **declarative** model, and it's the single most important Kubernetes idea.

### The four objects you'll use

```
Deployment  ("I want 3 copies of v1, updated safely")
   │ creates and manages
   ▼
ReplicaSet  ("keep exactly 3 pods alive")     ← you rarely touch this directly
   │ creates
   ▼
Pod  Pod  Pod   (each runs one container of hello-api:v1)
   ▲   ▲   ▲
   └───┴───┴── Service  ("one stable address that load-balances across those pods")
```

| Object         | What it is                                                              | Why it exists                                                          |
| -------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **Pod**        | The smallest unit: one (or a few) containers running together           | Pods are disposable — they die and get replaced with new names and IPs |
| **ReplicaSet** | Keeps N identical pods alive                                            | Self-healing                                                           |
| **Deployment** | Manages ReplicaSets so you can roll out new versions safely             | Zero-downtime updates and rollback                                     |
| **Service**    | A fixed name + IP that forwards to whichever pods are healthy right now | Pods keep changing IPs, so clients need something stable               |

### Read the desired state: `k8s/deployment.yaml`

Every Kubernetes file has the same four top-level keys: `apiVersion`, `kind`, `metadata`, `spec`. `spec` is "what I want".

```yaml
apiVersion: apps/v1
kind: Deployment # What type of object this is
metadata:
  name: hello-api # Its name
spec:
  replicas: 3 # WANT: 3 pods
  selector:
    matchLabels:
      app: hello-api # "The pods I manage are the ones labelled app=hello-api"
  strategy:
    type: RollingUpdate # Replace pods gradually during updates
    rollingUpdate:
      maxUnavailable: 0 # Never drop below 3 ready pods during an update
      maxSurge: 1 # May temporarily run 1 extra (so 4) while updating
  template: # The blueprint for each pod
    metadata:
      labels:
        app: hello-api # Each pod gets this label → matches the selector above
    spec:
      containers:
        - name: hello-api
          image: hello-api:v1 # Which image to run
          imagePullPolicy: IfNotPresent # Use a local copy if present; don't download
          ports:
            - containerPort: 8000
          env:
            - name: BREAK_READY
              value: "false" # The switch for the broken-release experiment
          livenessProbe: # "Is it stuck?" Fail → Kubernetes RESTARTS the container
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 5 # Wait 5s after start before first check
            periodSeconds: 10 # Then check every 10s
          readinessProbe: # "Should it get traffic?" Fail → removed from Service, NOT restarted
            httpGet: { path: /ready, port: 8000 }
            initialDelaySeconds: 3
            periodSeconds: 5
          resources:
            requests: { cpu: 50m, memory: 64Mi } # Guaranteed minimum (used for scheduling)
            limits: { cpu: 250m, memory: 128Mi } # Hard maximum (exceed memory → killed)
```

**Labels and selectors are the glue.** Kubernetes objects don't point at each other by name — they find each other by labels. The Deployment manages "pods labelled `app: hello-api`", and the Service sends traffic to "pods labelled `app: hello-api`". If a label is misspelled, things silently stop connecting. This is the #1 beginner bug.

**`50m` CPU** means 50 *milli*cores = 5% of one CPU core. **`64Mi`** = 64 mebibytes of memory.

### Read `k8s/service.yaml`

```yaml
kind: Service
metadata:
  name: hello-api # Other pods can reach it at http://hello-api
spec:
  selector:
    app: hello-api # Send traffic to pods with this label
  ports:
    - port: 80 # The Service listens on port 80...
      targetPort: 8000 # ...and forwards to port 8000 on the pods
```

The Service is Kubernetes' built-in **load balancer** — the same idea as SD 2, inside the cluster.

---

### 2.1 Create a cluster and give it your image

```bash
kind create cluster --name lab
```

Creates a one-node Kubernetes cluster (inside a Docker container) called `lab`, and points kubectl at it. Takes about a minute.

```bash
kubectl get nodes
```

| Piece         | Meaning                     |
| ------------- | --------------------------- |
| `kubectl get` | List objects of a type      |
| `nodes`       | The machines in the cluster |

**You'll see:** one node, `lab-control-plane`, status `Ready`.

```bash
kind load docker-image hello-api:v1 --name lab
```

**Why this is needed:** your image lives in Docker on your laptop. The cluster's node is a _separate_ container with its own image store, so it can't see it. This command copies the image in. In a company, you'd instead **push** the image to a registry (Docker Hub, GCR, ECR) and the cluster would download it. That's also why the YAML says `imagePullPolicy: IfNotPresent` — "use the copy I already have, don't try to download".

### 2.2 Apply the desired state

```bash
kubectl apply -f k8s/
```

| Piece     | Meaning                                                                                           |
| --------- | ------------------------------------------------------------------------------------------------- |
| `apply`   | "Make the cluster match these files." Creates objects that don't exist, updates ones that changed |
| `-f k8s/` | **f**ile(s): every YAML file in the `k8s/` folder                                                 |

**You'll see:** `deployment.apps/hello-api created` and `service/hello-api created`.

```bash
kubectl get pods -o wide
```

`-o wide` = output with extra columns (node, pod IP).

**You'll see:** 3 pods named like `hello-api-7d9f8c6b5-x2k4p`. The middle part identifies the ReplicaSet, the end is random. Look at two columns:

- `READY 1/1` — 1 of 1 containers passed readiness
- `STATUS Running`

If they show `0/1` for a few seconds, that's the readiness probe's initial delay — wait.

```bash
kubectl get svc
```

`svc` is short for `services`. **You'll see** `hello-api` with a `CLUSTER-IP` — the stable internal address.

### 2.3 Load balancing you can see

```bash
kubectl port-forward svc/hello-api 8080:80
```

**The idea:** the Service's IP only exists _inside_ the cluster. `port-forward` opens a temporary tunnel from your laptop's port 8080 to the Service's port 80. Leave it running and, in another terminal:

```bash
curl localhost:8080/
```

**Gotcha:** run it several times and the `pod` name often stays the same. That's because port-forward picks one pod and sticks to it — it's a debugging tunnel, not real traffic.

To see real load balancing, make a request from _inside_ the cluster, the way another service would:

```bash
kubectl run tmp --rm -it --image=curlimages/curl --restart=Never -- \
  sh -c 'for i in 1 2 3 4 5 6; do curl -s hello-api/; echo; done'
```

| Piece                     | Meaning                                                                          |
| ------------------------- | -------------------------------------------------------------------------------- |
| `kubectl run tmp`         | Start a one-off pod called `tmp`                                                 |
| `--image=curlimages/curl` | A tiny image that has `curl` in it                                               |
| `--rm`                    | Delete the pod when it finishes                                                  |
| `-it`                     | Interactive, so you see the output                                               |
| `--restart=Never`         | Run once, don't restart                                                          |
| `-- sh -c '...'`          | Everything after `--` is the command to run inside: a loop of 6 requests         |
| `hello-api/`              | Inside the cluster, a Service is reachable by its **name** (Kubernetes runs DNS) |

**You'll see:** 6 responses, with **different pod names**. That's the Service balancing traffic across pods.

### 2.4 Self-healing

```bash
kubectl get pods                 # copy one pod name
kubectl delete pod <pod-name>    # kill it
kubectl get pods -w              # -w = watch: keep printing changes live
```

**You'll see:** the deleted pod go `Terminating`, and almost immediately a **new pod with a new name** appear and become `Running`. Nobody told Kubernetes to do this — the ReplicaSet noticed "want 3, have 2" and fixed it. **Ctrl+C** stops watching.

**The interview point:** this is the desired-state loop. Kubernetes constantly compares _desired_ (3) with _actual_ (2) and acts on the difference.

### 2.5 Horizontal scaling

```bash
kubectl scale deployment hello-api --replicas=5
kubectl get pods
kubectl scale deployment hello-api --replicas=3
```

You changed the desired number; Kubernetes added 2 pods, then removed 2. That's SD 2's horizontal scaling in one command. Later (week of 19 Oct) you'll make this automatic with an HPA.

### 2.6 A zero-downtime rolling update

**The idea:** you have v1 running and want v2 without users noticing. With `maxUnavailable: 0` and `maxSurge: 1`, Kubernetes does this loop: start 1 new v2 pod → wait for it to pass **readiness** → kill 1 old v1 pod → repeat until all 3 are v2.

1. In `app/main.py`, change `VERSION = "v1"` to `VERSION = "v2"`.
2. Build and load the new image:
   ```bash
   cd app && docker build -t hello-api:v2 . && cd ..
   kind load docker-image hello-api:v2 --name lab
   ```
   (`&&` = "run the next command only if this one succeeded".)
3. Tell the Deployment to use it:
   ```bash
   kubectl set image deployment/hello-api hello-api=hello-api:v2
   ```
   | Piece                    | Meaning                                                                 |
   | ------------------------ | ----------------------------------------------------------------------- |
   | `set image`              | Change the image of a container in a Deployment                         |
   | `deployment/hello-api`   | Which Deployment                                                        |
   | `hello-api=hello-api:v2` | `<container name>=<new image>` — the container name comes from the YAML |
4. Watch it happen:
   ```bash
   kubectl rollout status deployment/hello-api
   kubectl get pods
   ```
   **You'll see** messages like "1 of 3 updated replicas are available…" until it completes. Pod names change completely because a **new ReplicaSet** was created for v2.

**Rollback:**

```bash
kubectl rollout history deployment/hello-api   # list revisions
kubectl rollout undo deployment/hello-api      # go back to the previous one
```

Rollback is fast because the old ReplicaSet still exists (scaled to 0) — Kubernetes just scales it back up.

**A note on honesty:** `kubectl set image` changes the cluster but **not** your YAML file. The file still says v1. That mismatch — "what's in Git vs what's actually running" — is exactly the problem ArgoCD solves in Part 3.

### 2.7 Experiment: readiness saves you from a bad release

**The idea:** a release where the app starts but isn't actually able to serve. Without readiness probes, Kubernetes would kill the healthy old pods and send users to broken ones.

1. In `k8s/deployment.yaml`, set `image: hello-api:v2` and `BREAK_READY` to `"true"`.
2. Apply and watch:
   ```bash
   kubectl apply -f k8s/
   kubectl get pods -w
   ```

**You'll see:** one new pod stuck at `0/1` READY forever, and the old pods **still running**. Because `maxUnavailable: 0`, Kubernetes refuses to remove a healthy pod until a new one is ready — and the new one never becomes ready. The rollout stalls; users keep getting served by the old version.

Look deeper:

```bash
kubectl describe pod <the-stuck-pod>
```

`describe` shows an object's full details plus **Events** at the bottom — the first place to look when anything goes wrong. You'll see `Readiness probe failed: HTTP probe failed with statuscode: 503`.

**Fix it:** set `BREAK_READY` back to `"false"` (keep v2), then `kubectl apply -f k8s/` again. The rollout completes.

**Contrast:** if this had been the _liveness_ probe failing, Kubernetes would **restart** the container over and over (`CrashLoopBackOff`). Readiness = "stop sending me traffic"; liveness = "restart me".

### 2.8 Hand over to ArgoCD

```bash
kubectl delete -f k8s/
```

Deletes everything those files created. From now on, you don't run `kubectl apply` — ArgoCD will.

### Check yourself

1. In your own words: what does "desired state" mean?
2. How does a Service know which pods to send traffic to?
3. During the rolling update, why did the pod count briefly go to 4?
4. Readiness vs liveness: what does Kubernetes do when each fails?
5. Why did you need `kind load`, and what replaces it in a company?

---

## Part 3 — ArgoCD: deploying from Git (60–90 min)

### The idea: GitOps

In Part 2, _you_ ran `kubectl apply` and `kubectl set image`. Problems with that in a real team:

- Nobody knows who changed what, or when.
- What's running can drift away from what's in Git (you saw this in 2.6).
- Rolling back means remembering what the old state was.

**GitOps** fixes this with one rule: **Git is the single source of truth for what should run.** You change the YAML in Git; a tool inside the cluster notices and applies it. Every deploy is a commit — reviewed, logged, and revertible.

**ArgoCD** is that tool. It runs inside your cluster and loops forever:

```
1. Read the YAML in the Git repo        (desired state)
2. Read what's running in the cluster    (live state)
3. Compare them
   - Same      → "Synced"
   - Different → "OutOfSync" → apply Git's version (if auto-sync is on)
```

Notice: it's the **same desired-state loop** as Kubernetes itself, just one level up — Git → cluster, instead of Deployment → pods.

**How this connects to your Osmos experience:** the "one-click deploy" you used is ArgoCD's **Sync** button. Clicking it says "make the cluster match Git now".

### 3.1 Put the lab on GitHub

ArgoCD reads from a Git repo, so the YAML has to live on GitHub. Create a **public** repo called `k8s-argocd-lab` (public so ArgoCD can read it without passwords). Then, from the lab root:

```bash
git init                         # make this folder a Git repo
git add .                        # stage every file
git commit -m "Initial lab"      # save a snapshot
git branch -M main               # name the branch "main"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/k8s-argocd-lab.git
                                 # tell Git where GitHub's copy lives
git push -u origin main          # upload; -u remembers origin/main for future pushes
```

Then edit `argocd/application.yaml`, replace `YOUR_GITHUB_USERNAME`, and commit + push again.

**Important:** the YAML in Git should end in a clean state — `image: hello-api:v1` and `BREAK_READY: "false"` — so ArgoCD starts from v1.

### 3.2 Install ArgoCD into the cluster

```bash
kubectl create namespace argocd
```

A **namespace** is a folder inside the cluster that keeps objects separate. ArgoCD's own pods go in `argocd`; your app stays in `default`.

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

Same `apply` as before — but the file is ArgoCD's official install YAML, read straight from a URL. `-n argocd` = put it in the `argocd` namespace. ArgoCD is itself just pods, Deployments and Services.

```bash
kubectl get pods -n argocd -w
```

Wait until every pod is `Running` (2–4 minutes), then Ctrl+C.

### 3.3 Open the ArgoCD UI

ArgoCD generates an admin password and stores it in a **Secret** (Kubernetes' object for sensitive values). Secrets are stored base64-encoded — _encoded_, not encrypted — so you decode it:

**Mac/Linux:**

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d; echo
```

| Piece                                    | Meaning                                                      |
| ---------------------------------------- | ------------------------------------------------------------ |
| `get secret argocd-initial-admin-secret` | Fetch that Secret                                            |
| `-o jsonpath="{.data.password}"`         | Print only the `password` field                              |
| `\| base64 -d`                           | Pipe it into a decoder                                       |
| `; echo`                                 | Print a newline so your prompt doesn't stick to the password |

**Windows PowerShell:**

```powershell
$p = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
```

Pass: i9etq-jVUjpasK-q

Open a tunnel to the UI (keep this terminal open):

```bash
kubectl port-forward svc/argocd-server -n argocd 8443:443
```

Go to **https://localhost:8443**, accept the certificate warning (it's self-signed — fine locally), log in as `admin` with the password.

### 3.4 Tell ArgoCD what to watch: `argocd/application.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application # An ArgoCD object, not a built-in Kubernetes one
metadata:
  name: hello-api
  namespace: argocd # ArgoCD's own objects live in its namespace
spec:
  project: default
  source: # WHERE the desired state lives
    repoURL: https://github.com/YOUR_GITHUB_USERNAME/k8s-argocd-lab.git
    targetRevision: main # which branch
    path: k8s # which folder of YAML
  destination: # WHERE to apply it
    server: https://kubernetes.default.svc # "this same cluster"
    namespace: default
  syncPolicy:
    automated: # Sync automatically when Git changes (no button needed)
      prune: true # Delete things from the cluster that were deleted from Git
      selfHeal: true # Undo manual changes made directly to the cluster
```

In one sentence: **"Keep the `default` namespace of this cluster matching the `k8s/` folder of this repo's `main` branch."**

```bash
kubectl apply -f argocd/application.yaml
```

**You'll see** in the UI: a `hello-api` tile. Click it — it draws the whole tree: Deployment → ReplicaSet → 3 Pods, plus the Service. Status turns **Synced** and **Healthy**. You never ran `kubectl apply -f k8s/` — ArgoCD did it from Git.

### 3.5 The real deploy: change Git, not the cluster

Make sure `hello-api:v2` is loaded into kind (you did it in 2.6; if you recreated the cluster, rerun `kind load docker-image hello-api:v2 --name lab`).

1. In `k8s/deployment.yaml`, change `image: hello-api:v1` → `image: hello-api:v2`.
2. Commit and push:
   ```bash
   git commit -am "Deploy v2"     # -a = include all changed tracked files, -m = message
   git push
   ```
3. In the UI, click **Refresh**. ArgoCD checks Git every ~3 minutes on its own; Refresh just checks now.
4. Watch the tree: new pods appear, old ones go — the same rolling update from 2.6, but triggered by a commit.

**That's the deploy.** No kubectl, no clicking through servers. Who deployed? Check the commit. When? Check the commit. What changed? Check the diff.

**One-click mode:** if you remove `automated:` from `syncPolicy`, ArgoCD stops applying changes itself. After a push, the app shows **OutOfSync**, and a human presses **Sync**. That's the button you used at Osmos — useful when a team wants a person to approve each production deploy.

### 3.6 Rollback, two ways

**The Git way (preferred):**

```bash
git revert HEAD     # make a NEW commit that undoes the last one
git push
```

ArgoCD sees Git now says v1, and rolls back. History stays complete: "deployed v2", then "reverted v2".

**The UI way:** App → **History and Rollback** → choose an earlier revision. **Gotcha:** this is disabled while auto-sync is on — otherwise ArgoCD would immediately re-apply what's in Git and undo your rollback. Explaining _why_ it's disabled is a strong interview detail.

### 3.7 Self-healing against manual changes

Pretend someone "quickly fixes" production by hand:

```bash
kubectl scale deployment hello-api --replicas=1
kubectl get pods -w
```

**You'll see:** pods drop toward 1, then ArgoCD notices "live ≠ Git" and scales back to 3 within seconds. That's `selfHeal: true`. Without it, the app would just show **OutOfSync** and wait for someone to sync.

**The interview point:** this makes Git the _only_ way to change production. Manual edits don't stick.

### 3.8 Clean up

```bash
kind delete cluster --name lab
```

Deletes the whole cluster (the Docker container). Your files and GitHub repo are untouched; `kind create cluster` gets you a fresh one.

### Check yourself

1. What is GitOps, in one sentence?
2. What does ArgoCD compare, and how often?
3. Why is `git revert` better than the UI rollback button?
4. What do `prune` and `selfHeal` each do?
5. Where does the "one-click deploy" from your old job fit in this picture?

---

## Part 4 — Say it out loud (20 min)

Answer each in under a minute, no notes:

1. Pod vs Deployment vs Service?
2. Liveness vs readiness — what happens when each fails?
3. Walk me through a rolling update. How does Kubernetes avoid downtime?
4. What is GitOps, and why deploy from Git instead of `kubectl apply`?
5. How do you roll back with ArgoCD, and why might the rollback button be disabled?
6. Someone edits production with kubectl. What does ArgoCD do?
7. In a real pipeline, what builds the image and updates the tag in Git?
   _(CI — e.g. GitHub Actions — builds and tests, pushes the image to a registry, then commits the new tag to the manifests repo. ArgoCD picks it up. **CI builds; CD deploys.**)_

### Describing your experience honestly

> "At OnlineSales I deployed services through ArgoCD as a one-click sync — the pipelines were owned by the platform team. To understand what's underneath, I built a lab from scratch: a FastAPI service with liveness and readiness probes, a rolling-update Deployment on a kind cluster, and ArgoCD syncing it from Git, including rollback and drift correction."

True, specific, and shows initiative. Don't claim you built the company pipelines.

---

## When something breaks

| You see                                                | It means                              | Do this                                                                          |
| ------------------------------------------------------ | ------------------------------------- | -------------------------------------------------------------------------------- |
| `Cannot connect to the Docker daemon`                  | Docker Desktop isn't running          | Start it, wait for the whale icon to settle                                      |
| Pod status `ErrImageNeverPull` or `ImagePullBackOff`   | The cluster can't find your image     | You forgot `kind load docker-image <image> --name lab`, or the tag is misspelled |
| Pod status `CrashLoopBackOff`                          | The container keeps crashing on start | `kubectl logs <pod>` to read the app's error                                     |
| Pod stuck at `0/1` READY                               | Readiness probe failing               | `kubectl describe pod <pod>` → read Events at the bottom                         |
| `port is already allocated` / `address already in use` | Something else uses that port         | Stop the other process, or use a different laptop port (e.g. `8081:80`)          |
| ArgoCD app `OutOfSync` and not changing                | Auto-sync is off, or Git isn't pushed | Check `git push` worked; click Refresh, then Sync                                |
| ArgoCD `ComparisonError` / repo not accessible         | Wrong repo URL or repo is private     | Fix `repoURL`, make the repo public                                              |

**The debugging order, always:** `get` (what state is it in?) → `describe` (what events happened?) → `logs` (what did the app say?).

---

## Command cheat sheet

### Docker

| Command                                 | What it does                                    |
| --------------------------------------- | ----------------------------------------------- |
| `docker build -t name:tag .`            | Build an image from the Dockerfile here         |
| `docker run --rm -p 8000:8000 name:tag` | Run a container, mapping laptop:container ports |
| `docker images`                         | List images on your laptop                      |
| `docker ps`                             | List running containers                         |

### kind

| Command                                      | What it does                        |
| -------------------------------------------- | ----------------------------------- |
| `kind create cluster --name lab`             | Make a local cluster                |
| `kind load docker-image name:tag --name lab` | Copy a local image into the cluster |
| `kind delete cluster --name lab`             | Delete the cluster                  |

### kubectl — look

| Command                                              | What it does                                      |
| ---------------------------------------------------- | ------------------------------------------------- |
| `kubectl get pods` / `svc` / `deployments` / `nodes` | List objects                                      |
| `kubectl get pods -o wide`                           | …with extra columns                               |
| `kubectl get pods -w`                                | …and keep watching for changes                    |
| `kubectl get pods -n argocd`                         | …in a specific namespace                          |
| `kubectl describe pod <name>`                        | Full details + Events (start here when debugging) |
| `kubectl logs <pod>`                                 | The app's output                                  |

### kubectl — change

| Command                                                   | What it does                      |
| --------------------------------------------------------- | --------------------------------- |
| `kubectl apply -f <file or folder>`                       | Make the cluster match the YAML   |
| `kubectl delete -f <file or folder>`                      | Remove what the YAML created      |
| `kubectl delete pod <name>`                               | Kill one pod (it'll be replaced)  |
| `kubectl scale deployment <name> --replicas=N`            | Change the number of pods         |
| `kubectl set image deployment/<name> <container>=<image>` | Change the image (rolling update) |

### kubectl — rollouts and access

| Command                                          | What it does                             |
| ------------------------------------------------ | ---------------------------------------- |
| `kubectl rollout status deployment/<name>`       | Follow an update until it finishes       |
| `kubectl rollout history deployment/<name>`      | List past revisions                      |
| `kubectl rollout undo deployment/<name>`         | Go back one revision                     |
| `kubectl port-forward svc/<name> LAPTOP:SERVICE` | Tunnel from your laptop into the cluster |

### git (for ArgoCD)

| Command                | What it does                                       |
| ---------------------- | -------------------------------------------------- |
| `git commit -am "msg"` | Commit all changed tracked files                   |
| `git push`             | Upload commits — this _is_ the deploy with ArgoCD  |
| `git revert HEAD`      | New commit that undoes the last one — the rollback |
