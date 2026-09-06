# GKE Private Cluster Access — IAP Bastion Host

Provides `kubectl` access to a private GKE cluster (where `privateEndpointEnforcementEnabled: true` blocks direct API server access) using:

- A **lightweight bastion VM** (`e2-micro`) inside the same VPC — no public IP
- **Google Cloud IAP (Identity-Aware Proxy)** — SSH tunnelled through Google's infrastructure without opening any firewall ports to the internet
- **OS Login** — SSH access is gated by Google identity; no SSH key management required

```
Your laptop
    │  gcloud compute ssh --tunnel-through-iap
    ▼
Google IAP (35.235.240.0/20)
    │  TCP:22  (firewall tag: bastion-iap)
    ▼
portkey-bastion  (10.0.0.18, no external IP)
    │  kubectl → 172.16.0.2:443  (private GKE API endpoint)
    ▼
portkey-aigw-cluster API server
```

---

## Prerequisites

| Tool | Install |
|---|---|
| `gcloud` CLI | `brew install --cask google-cloud-sdk` |

No other local tools are required — `kubectl` runs on the bastion, not your laptop.

---

## 1. Environment variables

```sh
export PROJECT_ID="mgollop-d974"
export CLUSTER_NAME="portkey-aigw-cluster"
export REGION="us-central1"
export ZONE="us-central1-a"
export VPC="portkey-net"
export SUBNET="portkey-net-nodes"
export BASTION_NAME="portkey-bastion"
export BASTION_SA="portkey-bastion-sa"
```

---

## 2. Enable IAP and OS Login APIs

```sh
gcloud services enable iap.googleapis.com oslogin.googleapis.com \
  --project=$PROJECT_ID
```

---

## 3. Create a service account for the bastion

The bastion uses this SA to authenticate to GKE. `container.developer` gives enough access to deploy and inspect workloads.

```sh
gcloud iam service-accounts create $BASTION_SA \
  --display-name="Portkey Bastion Host" \
  --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${BASTION_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/container.developer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${BASTION_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/container.clusterViewer"
```

---

## 4. Create the bastion VM

`e2-micro` in the same VPC/subnet as the GKE nodes. No external IP. The startup script installs `kubectl` and the GKE auth plugin.

```sh
cat > /tmp/bastion-startup.sh << 'EOF'
#!/bin/bash
set -e
apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl gnupg

# kubectl
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.31/deb/Release.key \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.31/deb/ /" \
  > /etc/apt/sources.list.d/kubernetes.list
apt-get update -y
apt-get install -y kubectl

# gke-gcloud-auth-plugin
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  > /etc/apt/sources.list.d/google-cloud-sdk.list
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
apt-get update -y
apt-get install -y google-cloud-sdk-gke-gcloud-auth-plugin

touch /var/log/startup-done
EOF

gcloud compute instances create $BASTION_NAME \
  --project=$PROJECT_ID \
  --zone=$ZONE \
  --machine-type=e2-micro \
  --network=$VPC \
  --subnet=$SUBNET \
  --no-address \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=20GB \
  --boot-disk-type=pd-standard \
  --tags=bastion-iap \
  --metadata=enable-oslogin=TRUE \
  --metadata-from-file=startup-script=/tmp/bastion-startup.sh \
  --service-account=${BASTION_SA}@${PROJECT_ID}.iam.gserviceaccount.com \
  --scopes=cloud-platform
```

Wait for the startup script to finish (≈2–3 min):

```sh
until gcloud compute ssh $BASTION_NAME \
  --zone=$ZONE --project=$PROJECT_ID \
  --tunnel-through-iap \
  --command="test -f /var/log/startup-done" \
  --quiet 2>/dev/null; do
  echo "waiting..."; sleep 15
done
echo "bastion ready"
```

---

## 5. Create the IAP firewall rule

Allow IAP's fixed source range (`35.235.240.0/20`) to reach the bastion on port 22. No other inbound internet access is granted.

```sh
gcloud compute firewall-rules create allow-iap-ssh-bastion \
  --project=$PROJECT_ID \
  --network=$VPC \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:22 \
  --source-ranges=35.235.240.0/20 \
  --target-tags=bastion-iap \
  --description="Allow IAP TCP tunnel to bastion for SSH"
```

---

## 6. Grant IAP access to users

Each person who needs cluster access needs these two roles:

```sh
# Replace with the user's Google identity
export USER_EMAIL="user@example.com"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="user:${USER_EMAIL}" \
  --role="roles/iap.tunnelResourceAccessor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="user:${USER_EMAIL}" \
  --role="roles/compute.osLogin"
```

| Role | Purpose |
|---|---|
| `roles/iap.tunnelResourceAccessor` | Allows opening an IAP TCP tunnel to the bastion |
| `roles/compute.osLogin` | Allows SSH login via Google identity (no SSH key needed) |

---

## 7. Configure kubectl on the bastion (first-time only)

Run this once after the VM is created. It configures kubectl to use the private GKE API endpoint:

```sh
gcloud compute ssh $BASTION_NAME \
  --zone=$ZONE \
  --project=$PROJECT_ID \
  --tunnel-through-iap \
  --command="gcloud container clusters get-credentials $CLUSTER_NAME \
    --region $REGION \
    --project $PROJECT_ID \
    --internal-ip"
```

---

## 8. Day-to-day usage

### Open an interactive shell

```sh
gcloud compute ssh portkey-bastion \
  --zone=us-central1-a \
  --project=mgollop-d974 \
  --tunnel-through-iap
```

Then run any `kubectl` command normally:

```sh
kubectl get pods -n airs-gw
kubectl rollout restart deployment/airs-gw -n airs-gw
kubectl logs -f deployment/airs-gw -n airs-gw
```

### Run a single command without opening a shell

```sh
gcloud compute ssh portkey-bastion \
  --zone=us-central1-a \
  --project=mgollop-d974 \
  --tunnel-through-iap \
  --command="kubectl get pods -n airs-gw"
```

### Copy files to/from the bastion (e.g. updated values.yml)

```sh
gcloud compute scp values.yml portkey-bastion:~/values.yml \
  --zone=us-central1-a \
  --project=mgollop-d974 \
  --tunnel-through-iap
```

---

## What was created

| Resource | Details |
|---|---|
| VM | `portkey-bastion` — `e2-micro`, `us-central1-a`, `10.0.0.18`, no external IP |
| Service account | `portkey-bastion-sa@mgollop-d974.iam.gserviceaccount.com` — `container.developer` + `container.clusterViewer` |
| Firewall rule | `allow-iap-ssh-bastion` — allows `35.235.240.0/20 → tcp:22` on tag `bastion-iap` |
| IAP/OS Login | `mgollop@paloaltonetworks.com` — `iap.tunnelResourceAccessor` + `compute.osLogin` |

---

## Teardown

```sh
gcloud compute instances delete portkey-bastion \
  --zone=us-central1-a --project=mgollop-d974 --quiet

gcloud compute firewall-rules delete allow-iap-ssh-bastion \
  --project=mgollop-d974 --quiet

gcloud iam service-accounts delete \
  portkey-bastion-sa@mgollop-d974.iam.gserviceaccount.com \
  --project=mgollop-d974 --quiet
```
