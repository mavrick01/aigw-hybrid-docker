# AIRS Gateway on GKE — WIF + Istio Setup Guide

Deploys the AIRS Gateway Helm chart onto an existing GKE cluster with:
- **Workload Identity Federation (WIF)** — no service account keys; the pod impersonates a Google Service Account at runtime
- **Istio service mesh** — Envoy sidecar, mTLS between services

---

## Prerequisites

| Tool | Install |
|---|---|
| `gcloud` CLI | `brew install --cask google-cloud-sdk` |
| `kubectl` | `brew install kubectl` |
| `helm` (v3+) | `brew install helm` |
| `istioctl` | `brew install istioctl` |

The GKE cluster must have:
- **Workload Identity** enabled (`--workload-pool=PROJECT_ID.svc.id.goog`)
- **GKE Metadata Server** on node pools (`--workload-metadata=GKE_METADATA`)
- **HTTP Load Balancing** add-on enabled (required for internal LB)

---

## 1. Environment variables

Set these once — all commands below reference them.

```sh
export PROJECT_ID="<PROJECT_ID>"
export CLUSTER_NAME="portkey-aigw-cluster"
export REGION="<REGION>"
export NAMESPACE="airs-gw"
export GSA_NAME="portkey-gateway-sa"        # Google Service Account
export KSA_NAME="gateway-sa"               # Kubernetes Service Account
```

---

## 2. Authenticate and connect to the cluster

```sh
gcloud auth application-default login
gcloud container clusters get-credentials $CLUSTER_NAME \
  --region $REGION \
  --project $PROJECT_ID
```

---

## 3. Create the Google Service Account

```sh
gcloud iam service-accounts create $GSA_NAME \
  --display-name="AIRS Gateway" \
  --project=$PROJECT_ID
```

### Grant Vertex AI access

```sh
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

### Bind the Kubernetes Service Account to the GSA (WIF)

This allows the KSA (`airs-gw/gateway-sa`) to impersonate the GSA at runtime:

```sh
gcloud iam service-accounts add-iam-policy-binding \
  ${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]"
```

---

## 4. Create the namespace

```sh
kubectl create namespace $NAMESPACE
```

---

## 5. Configure values.yml

The `values.yml` file controls the Helm deployment. The key additions for WIF and internal LB are the `serviceAccount` and `service.annotations` blocks.

```yaml
# Image version override (chart default may be older)
images:
  gatewayImage:
    tag: "2.17.0"

# Registry credentials for registry.portkey.ai
imageCredentials:
  - name: airsgatewayregistrycredentials
    create: true
    registry: "https://registry.portkey.ai"
    username: "<PROVIDED_BY_PORTKEY>"
    password: "<PROVIDED_BY_PORTKEY>"

# Kubernetes Service Account with WIF annotation.
# The annotation links this KSA to the GSA created in step 3,
# allowing the pod to obtain short-lived GCP credentials at runtime
# without any key files.
serviceAccount:
  create: true
  automount: true
  name: gateway-sa
  annotations:
    iam.gke.io/gcp-service-account: portkey-gateway-sa@mgollop-d974.iam.gserviceaccount.com

environment:
  create: true
  secret: true
  data:
    PORTKEY_CLIENT_AUTH: "<PROVIDED_BY_PORTKEY>"
    ORGANISATIONS_TO_SYNC: "<YOUR_ORG_UUID>"
    PORT: "8787"

# Internal GCP L4 load balancer — not reachable from the public internet.
service:
  type: LoadBalancer
  port: 80
  containerPort: 8787
  annotations:
    networking.gke.io/load-balancer-type: "Internal"
```

> **Why `serviceAccount`?** WIF requires three things to be wired together:
> 1. The node pool has `GKE_METADATA` mode — the metadata server on each node handles token exchange
> 2. The KSA has `iam.gke.io/gcp-service-account` annotation — tells the metadata server which GSA to impersonate
> 3. The IAM binding from step 3 — authorises the KSA to act as the GSA
>
> Without the annotation the pod runs as the `default` KSA which has no link to any GSA, so WIF silently falls back to looking for a key file.

---

## 6. Deploy the Helm chart

```sh
helm repo add airs-gw https://portkey-ai.github.io/airs-gw-helm
helm repo update

helm upgrade --install airs-gw airs-gw/airs-gw \
  -f values.yml \
  -n $NAMESPACE \
  --create-namespace
```

### Verify deployment

```sh
kubectl get pods -n $NAMESPACE
# Expected: 1/1 Running for airs-gw, 1/1 Running for redis

kubectl get svc airs-gw -n $NAMESPACE
# EXTERNAL-IP will be an internal RFC1918 address (e.g. 10.0.0.x)
```

---

## 7. Install Istio

```sh
istioctl install --set profile=default -y
```

Verify the control plane is up:

```sh
kubectl get pods -n istio-system
# istiod and istio-ingressgateway should be Running
```

---

## 8. Enable sidecar injection

Label the namespace so Istio automatically injects the Envoy sidecar into new pods:

```sh
kubectl label namespace $NAMESPACE istio-injection=enabled
```

### Exclude the GKE metadata server from Istio interception

The GKE metadata server (`169.254.169.254`) handles WIF token exchange. Istio's iptables rules would intercept these calls and break WIF, so it must be excluded:

```sh
kubectl patch deployment airs-gw -n $NAMESPACE --type=json -p='[
  {
    "op": "add",
    "path": "/spec/template/metadata/annotations",
    "value": {
      "traffic.sidecar.istio.io/excludeOutboundIPRanges": "169.254.169.254/32"
    }
  }
]'
```

### Restart the pod to inject the sidecar

```sh
kubectl rollout restart deployment/airs-gw -n $NAMESPACE
kubectl rollout status deployment/airs-gw -n $NAMESPACE
```

### Verify sidecar injection

```sh
kubectl get pods -n $NAMESPACE
# airs-gw pod should now show 2/2 READY (gateway + envoy sidecar)
```

---

## 9. Verify end-to-end

From the test VM or another pod in the VPC:

```sh
# Gateway internal LB IP
GW_IP=$(kubectl get svc airs-gw -n $NAMESPACE \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Health check
curl http://$GW_IP/v1/health

# LLM call through the gateway
curl http://$GW_IP/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-portkey-api-key: $AIGW_API_KEY" \
  -d '{
    "model": "@vertex/anthropic.claude-haiku-4-5@20251001",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is AI Gateway"}
    ],
    "max_tokens": 512
  }'
```

---

## Architecture

```
Client (test VM / internal service)
    │
    ▼ http (internal VPC only)
Internal L4 NLB  (10.0.0.16:80)
    │
    ▼
airs-gw pod
  ├── istio-proxy (Envoy sidecar) — mTLS, observability
  └── airs-gw container (port 8787)
        │  WIF token exchange (via GKE metadata server, bypasses Envoy)
        ▼
  portkey-gateway-sa GSA
        │  roles/aiplatform.user
        ▼
  Vertex AI
```

---

## Updating the gateway version

To upgrade to a newer image without changing the chart:

```sh
# Update the tag in values.yml, then:
helm upgrade airs-gw airs-gw/airs-gw \
  -f values.yml \
  -n $NAMESPACE
```

The deployment performs a rolling update — old pod stays up until the new one is healthy.
