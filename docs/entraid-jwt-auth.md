# EntraID (OIDC/JWT) Authentication for Claude Desktop

Configures Claude Desktop to authenticate against this self-hosted AIGW Gateway using Microsoft EntraID as the OIDC identity provider, with JWT bearer tokens validated by the gateway.

## Prerequisites

- This repo's `docker-compose.yml` stack running
  -  `JWT_ENABLED: ON` (already set — see [docker-compose.yml](../docker-compose.yml))
  - `JWT_LOCAL_AUTH_DEFAULT_SCOPES: completions.write,mcp.invoke` (already set — see [docker-compose.yml](../docker-compose.yml))- This is required as EntraID is not setting the scope
- An EntraID tenant with permissions to register an application (entra.microsoft.com)
- Claude Desktop installed, with the Developer menu enabled (step 1 below)

## 1. Enable the Developer menu in Claude Desktop

The 3rd-party provider / OIDC configuration UI referenced in step 6 lives behind Claude Desktop's Developer menu, which is hidden by default. Enable it before continuing (Help → Troubleshooting → Enable Developer Mode, or the equivalent toggle for your Claude Desktop version).
![Help → Troubleshooting → Enable Developer Mode](./Enable%20Developer%20Mode.png)

## 2. Register the application in EntraID

In the [Entra Admin Center](https://entra.microsoft.com):


1. Register a new application (App registrations → New registration).

![EntraID->App Registration](./App-Registration.png)
2. Add a redirect URI for the **loopback port Claude Desktop will use** — see step 6 (`http://localhost:8080` in this example).

![App-Configuration](./App-Configuration.png)
3. Note the **Application (client) ID** and the **Application tenant ID** (You will use it as the **OIDC Issuer URL**`https://login.microsoftonline.com/<tenant-id>/v2.0`) — both are needed in step 6.

![App-Details](./App-Details.png)

## 3. Add custom claims via a Claims Mapping Policy

The gateway requires specific claims in the ID token to identify the user and map them to the correct Portkey workspace. Because several of these claims (e.g. `onPremisesSamAccountName`) are not available in the standard EntraID token, they must be injected using an **EntraID Claims Mapping Policy** rather than the basic "optional claims" UI.

The `apply-claims-policy.sh` script in this repo creates and assigns the policy automatically. Run it once after registering the app:

```sh
bash apply-claims-policy.sh
```

The policy it creates has `IncludeBasicClaimSet: true` (standard claims such as `sub`, `oid`, `iss` are preserved) and injects the following additional claims:

| JWT claim | Source | Value / AD attribute |
|---|---|---|
| `jobtitle` | user attribute | `user.jobtitle` |
| `department` | user attribute | `user.department` |
| `uid` | user attribute | `user.onPremisesSamAccountName` |
| `mailnickname` | user attribute | `user.mailNickname` |
| `email_id` | user attribute | `user.mail` |
| `_user` | user attribute | `user.mailNickname` |
| `portkey_workspace` | static **or** user extension attribute | workspace slug, e.g. `ws-main-a-123456` |
| `portkey_oid` | static | `95cb67bf-6fb5-4581-ac72-db32e2bb7f2c` (= `ORGANISATIONS_TO_SYNC`) |

The most load-bearing claims are `email_id` (user identity) and `portkey_oid` (org mapping).

### Sourcing `portkey_workspace`

The script prompts you to choose one of two approaches:

| Option | How it works | When to use |
|---|---|---|
| **Static value** | The same workspace slug is embedded directly in the policy definition | All users land in a single workspace |
| **Extension attribute** | The claim is read from `extensionAttribute1`–`extensionAttribute15` on the user object | Different users (or user groups) need different workspaces |

The extension-attribute approach is the recommended path for multi-workspace deployments. Set the workspace slug on each user via Entra admin, Graph API, or on-prem AD (the attributes sync automatically). The Claims Mapping Policy then reads it per-user at token issuance — no policy change required when users move between workspaces.

> **Note on group-based sourcing:** Claims Mapping Policies don't support mapping a group membership directly to a single claim value — you'd get an array of GUIDs, not a workspace slug. If you want workspace assignment driven by group membership, the practical approach is to use [dynamic group rules](https://learn.microsoft.com/en-us/entra/identity/users/groups-dynamic-membership) to keep group membership in sync, and write the workspace slug back to an `extensionAttribute` on the user via a provisioning flow or Logic App.

> **Note:** `acceptMappedClaims` is also enabled on the app registration by the script. Without this, EntraID returns a 400 when exchanging the token.

## 4. Configure the Vertex integration

In the AIGW control plane, add an integration for `@vertex` and select the models you want available through this gateway.

## 5. Create a AIGW config

Create a config that routes to the Vertex integration, e.g.:

```json
{
  "retry": {
    "attempts": 3
  },
  "cache": {
    "mode": "simple"
  },
  "provider": "@vertex"
}
```

Save it and note the generated **config ID** — it's used in step 6.

## 6. Configure the 3rd-party (OIDC) provider in Claude Desktop

In Claude Desktop's Developer menu, add a 3rd-party provider pointing at the local hybrid gateway container, with OIDC authentication:

| Field | Value |
|---|---|
| Base URL | This gateway's URL (e.g. `http://localhost:8787`) |
| Client ID | The Application (client) ID from step 2 |
| Issuer URL | The EntraID tenant issuer URL from step 2 |
| Bearer token type | ID Token |
| Scopes | `openid profile email offline_access` |
| Redirect port | `8080` (must match the redirect URI registered in step 2) |

here is a sample configuration:
```
{
  "inferenceGatewayBaseUrl": "http://127.0.0.1:8787",
  "inferenceCustomHeaders": "[redacted]",
  "inferenceGatewayOidcAuthFlow": "browser",
  "inferenceGatewayOidc": {
    "clientId": "9629c204-88db-4e7e-92a8-dda28e451409",
    "issuer": "https://login.microsoftonline.com/<TenantID>/v2.0",
    "bearerTokenType": "id_token",
    "scopes": "openid profile email offline_access",
    "appendOfflineAccess": true,
    "redirectPort": 8080
  },
  "chatTabEnabled": true,
  "modelDiscoveryEnabled": true,
  "inferenceModels": [],
  "inferenceProvider": "gateway",
  "inferenceCredentialKind": "interactive"
}
```


Finally, add a custom header so every request carries the AIGW config from step 5:

```
x-portkey-config: <config-id>
```

## Verifying it works

1. In Claude Desktop, trigger the OIDC login flow for the provider you just added — it should open a browser to EntraID, authenticate, and redirect back to `localhost:8080`.
2. Send a message through Claude Desktop using this provider.
3. Confirm requests are landing in AIGW's control plane logs/analytics for the mapped workspace.
  ![SCM-Logs](SCM-Logs.png) 

## Troubleshooting

- **401 Unauthorized** — verify `JWT_ENABLED: ON` and `JWT_LOCAL_AUTH_DEFAULT_SCOPES` are set on the gateway (see [docker-compose.yml](../docker-compose.yml)), and that the ID token actually contains the custom claims from step 3 (decode it at [jwt.ms](https://jwt.ms) to check).
- **Org/workspace not resolved** — double check `portkey_oid` in the token matches this deployment's `ORGANISATIONS_TO_SYNC` env var exactly. Decode the token at [jwt.ms](https://jwt.ms) to inspect the raw claims.
- **Redirect fails / stuck on EntraID** — confirm the redirect URI registered in EntraID (step 2) exactly matches the redirect port configured in Claude Desktop (step 6), including `http://localhost:<port>`.
