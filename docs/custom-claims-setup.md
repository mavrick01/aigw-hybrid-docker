# Adding a Custom Claim to Azure AD JWT Tokens

## Goal

Add `email_id: <user's email address>` to JWTs issued for the **mgollop Claude gateway** app (`9629c204-88db-4e7e-92a8-dda28e451409`) when users log in via OIDC.

## How it works

Azure AD does not include custom attributes in tokens by default. To add them you need:

1. A **Claims Mapping Policy** that defines which directory attributes map to which JWT claim names
2. The policy **assigned** to the app's Service Principal
3. The app registration opted in via **`acceptMappedClaims: true`** — without this Azure AD returns HTTP 400 on token exchange

## Steps

### 1. Create the Claims Mapping Policy

POST to Microsoft Graph:

```
POST https://graph.microsoft.com/v1.0/policies/claimsMappingPolicies
```

The policy definition maps:

| Source attribute | JWT claim |
|---|---|
| `mail` | `email_id` |
| `jobtitle` | `jobtitle` |
| `department` | `department` |
| `onpremisessamaccountname` | `uid` |
| `mailnickname` | `mailnickname` |

`IncludeBasicClaimSet: true` preserves the standard claims (sub, iss, iat, etc.).

### 2. Enable `acceptMappedClaims` on the app registration

Without this the token exchange fails with HTTP 400. Patch the application object (using the **object ID**, not the client/app ID):

```bash
appObjectId=$(az ad app show --id 9629c204-88db-4e7e-92a8-dda28e451409 --query "id" -o tsv)

az rest --method PATCH \
    --uri "https://graph.microsoft.com/v1.0/applications/$appObjectId" \
    --headers "Content-Type=application/json" \
    --body '{"api": {"acceptMappedClaims": true}}'
```

### 3. Assign the policy to the Service Principal

The Service Principal ObjectId for this app is `53fa8962-86e0-42f2-a26f-d98ef133409a`.
The Claims Mapping Policy ID created in step 1 is `27adeba5-d8b2-4e15-89cb-639d9e822ae1`.

```bash
az rest --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/53fa8962-86e0-42f2-a26f-d98ef133409a/claimsMappingPolicies/\$ref" \
    --headers "Content-Type=application/json" \
    --body '{"@odata.id": "https://graph.microsoft.com/v1.0/policies/claimsMappingPolicies/27adeba5-d8b2-4e15-89cb-639d9e822ae1"}'
```

### 4. Verify the policy is assigned

```bash
az rest --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/53fa8962-86e0-42f2-a26f-d98ef133409a/claimsMappingPolicies"
```

## Automate everything

`apply-claims-policy.sh` handles all steps above in order. Run it with:

```bash
bash apply-claims-policy.sh
```

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| Token exchange HTTP 400 | `acceptMappedClaims` not set | Run step 2 above |
| `Resource '' does not exist` | Used client ID instead of object ID in Graph PATCH | Use `az ad app show --query id` to get the object ID |
| Redirect URI mismatch HTTP 400 | Registered URI doesn't exactly match what Claude Desktop sends | In App Registration → Authentication, ensure `http://127.0.0.1:8080/callback` is listed under Mobile and desktop applications |
| `email_id` missing from JWT | `mail` attribute empty for the user in Azure AD | Check the user's profile — set their email address in the directory |

## Key IDs

| Item | ID |
|---|---|
| App (client) ID | `9629c204-88db-4e7e-92a8-dda28e451409` |
| Service Principal Object ID | `53fa8962-86e0-42f2-a26f-d98ef133409a` |
| Claims Mapping Policy ID | `27adeba5-d8b2-4e15-89cb-639d9e822ae1` |
