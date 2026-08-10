# EntraID (OIDC/JWT) Authentication for Claude Desktop

Configures Claude Desktop to authenticate against this self-hosted Portkey Gateway using Microsoft EntraID as the OIDC identity provider, with JWT bearer tokens validated by the gateway.

## Prerequisites

- This repo's `docker-compose.yml` stack running, with `JWT_ENABLED: ON` (already set — see [docker-compose.yml](../docker-compose.yml))
- An EntraID tenant with permissions to register an application (entra.microsoft.com)
- Claude Desktop installed, with the Developer menu enabled (step 1 below)

## 1. Enable the Developer menu in Claude Desktop

The 3rd-party provider / OIDC configuration UI referenced in step 6 lives behind Claude Desktop's Developer menu, which is hidden by default. Enable it before continuing (Settings → enable Developer mode, or the equivalent toggle for your Claude Desktop version).

## 2. Register the application in EntraID

In the [Entra Admin Center](https://entra.microsoft.com):

1. Register a new application (App registrations → New registration).
2. Add a redirect URI for the **loopback port Claude Desktop will use** — see step 6 (`http://localhost:8080` in this example).
3. Note the **Application (client) ID** and the tenant's **OIDC Issuer URL** (`https://login.microsoftonline.com/<tenant-id>/v2.0`) — both are needed in step 6.

## 3. Add custom claims

Portkey needs certain claims present in the ID token to map an authenticated EntraID user to a Portkey workspace/organisation. In EntraID, add these via **Token configuration → Add optional claim** (and/or a claims-mapping policy if the claim isn't natively available on the token):

| Claim | Example value | Purpose |
|---|---|---|
| `email_id` | `<user@yourcompany.com>` | User's email, used for identification |
| `portkey_workspace` | `<your-workspace-slug>` | Maps the user into a specific Portkey workspace |
| `portkey_oid` | `<your-portkey-org-uuid>` | Must match this deployment's `ORGANISATIONS_TO_SYNC` |
| `_user` | `<your-mailnickname>` | Internal user identifier |
| `department` | `<your-department>` | Optional metadata claim |
| `mailnickname` | `<your-mailnickname>` | EntraID mail alias |

Not all of these were confirmed strictly required — `email_id` and `portkey_oid` are the most load-bearing (identity + org mapping). Trim unused claims once you've verified login works, or if you want to keep the token slim.

## 4. Configure the Vertex integration

In the Portkey control plane, add an integration for `@vertex` and select the models you want available through this gateway.

## 5. Create a Portkey config

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

Finally, add a custom header so every request carries the Portkey config from step 5:

```
x-portkey-config: <config-id>
```

## Verifying it works

1. In Claude Desktop, trigger the OIDC login flow for the provider you just added — it should open a browser to EntraID, authenticate, and redirect back to `localhost:8080`.
2. Send a message through Claude Desktop using this provider.
3. Check the gateway logs for the authenticated request:
   ```sh
   docker compose logs -f portkey-gateway
   ```
4. Confirm requests are landing in Portkey's control plane logs/analytics for the mapped workspace.

## Troubleshooting

- **401 Unauthorized** — verify `JWT_ENABLED: ON` and `JWT_LOCAL_AUTH_DEFAULT_SCOPES` are set on the gateway (see [docker-compose.yml](../docker-compose.yml)), and that the ID token actually contains the custom claims from step 3 (decode it at [jwt.ms](https://jwt.ms) to check).
- **Org/workspace not resolved** — double check `portkey_oid` in the token matches this deployment's `ORGANISATIONS_TO_SYNC` env var exactly.
- **Redirect fails / stuck on EntraID** — confirm the redirect URI registered in EntraID (step 2) exactly matches the redirect port configured in Claude Desktop (step 6), including `http://localhost:<port>`.
