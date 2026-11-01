# Contract Master v1 — permissions and RBAC

Current state. See [00](contract_master_v1_00_overview.md) for the governing rules.

## Reused permissions

| Constant | Value | Gates |
|---|---|---|
| `CONTRACT_MASTER_VIEW` | `dms.contract.master.view` | contract record reads |
| `CONTRACT_MASTER_MANAGE` | `dms.contract.master.manage` | contract record create/update; classification confirmation |
| `DOCUMENT_VIEW` | `dms.document.view` | canonical content |
| `DOCUMENT_DOWNLOAD` | `dms.document.download` | canonical bytes |
| `DOCUMENT_UPLOAD` | `dms.document.upload` | uploads |
| `DMS_ADMIN` | `dms.admin` | operator **routes** only |

## Two new permissions

| Constant | Value | Gates |
|---|---|---|
| `CONTRACT_APPLICABILITY_MANAGE` | `dms.contract.applicability.manage` | applicability lifecycle **and** legal effects |
| `CONTRACT_CATALOGUE_BROWSE` | `dms.contract.catalogue.browse` | the organisation catalogue, including organisation-scope creation |

Both are deny-by-default. Evidence *use* needs no new permission — it is gated by
applicability plus the existing document permissions.

## The bulk-grant exclusion — load-bearing

`CLIENT_DMS_PERMISSIONS` does **three unrelated jobs at once**:

1. it is the DMS permission **catalogue** (the canonical permission list is
   assembled from it, and seed-catalogue tests fail if a canonical permission is
   missing);
2. it is the **entitlement scope set** — a permission outside it is classified
   "not entitlement scoped" and **bypasses the subscription gate entirely**;
3. it is the **bulk role grant**, merged wholesale into `superadmin`,
   `orgadmin`, `projectadmin` and `contractmgr_org`.

The two new permissions must be in (1) and (2) and must **not** be in (3).

**Therefore both permissions stay in `CLIENT_DMS_PERMISSIONS`.** Removing them —
the intuitive way to keep Project Admin out — would leave them unseeded and
unassignable, and would create a subscription-gate bypass, making them the only
contract permissions an expired-subscription tenant could still exercise.

The bulk merge is narrowed instead:

```
ORG_TIER_ONLY_PERMISSIONS = {
    "dms.contract.applicability.manage",
    "dms.contract.catalogue.browse",
}
```

`projectadmin` receives `CLIENT_DMS_PERMISSIONS - ORG_TIER_ONLY_PERMISSIONS`.
`superadmin`, `orgadmin` and `contractmgr_org` keep the full list — all three are
organisation tier, and an independent seed test requires `orgadmin` to hold every
entry.

**Project Admin isolation is achieved by the exclusion set, never by list
omission.** Any implementation that satisfies the isolation requirement by
removing a permission from `CLIENT_DMS_PERMISSIONS` reintroduces the entitlement
hole. This needs two separate tests — one that Project Admin lacks both, one that
both remain entitlement-scoped — because either alone passes while the other
fails.

## Permissions that do not isolate

- **`CONTRACT_MASTER_VIEW` / `CONTRACT_MASTER_MANAGE` are bulk-granted to
  `projectadmin`.** They cannot protect a zero-applicability organisation
  catalogue item from a project-tier actor. Catalogue isolation requires
  `CONTRACT_CATALOGUE_BROWSE`.
- **`DMS_ADMIN` is inside `CLIENT_DMS_PERMISSIONS`, so every Project Admin holds
  it.** It gates routes; it is never the target authority. Migration promotion
  and initial applicability must never be gated on it alone.
- **Role `scope` metadata is written but never read at runtime**, and defaults
  fail-open when absent. It is not an authorisation boundary and Contract Master
  must not depend on it. This is a generic RBAC concern, recorded so no future
  feature adopts it believing it is enforced.

## Composition

Authorisation composes with **AND, never OR**, and always by capability at a
resource scope — never by role name.

| Action | Requires |
|---|---|
| view contract records | `CONTRACT_MASTER_VIEW` |
| upload, project scope | `DOCUMENT_UPLOAD` on that exact project |
| upload, organisation scope | `DOCUMENT_UPLOAD` + `CONTRACT_MASTER_MANAGE` + `CONTRACT_CATALOGUE_BROWSE`, all at organisation scope |
| browse the organisation catalogue | `CONTRACT_CATALOGUE_BROWSE` |
| confirm or correct classification | `CONTRACT_MASTER_MANAGE` |
| apply / withdraw / supersede | `CONTRACT_APPLICABILITY_MANAGE` |
| create or amend a legal effect | `CONTRACT_APPLICABILITY_MANAGE` |
| view or download content | `DOCUMENT_VIEW` / `DOCUMENT_DOWNLOAD` |
| migration inventory, review, adjudicate | migration operator capability — confers **no** legal authority |
| promotion | the above **plus** the target permissions for everything it writes |

## Scope resolution

The contract scope resolver sits **between generic authorisation and the document
layer**. Generic denial propagates untouched — the resolver never converts a
generic 403 into something softer.

- The generic check is a **precondition type**, so it cannot be skipped by
  construction rather than by convention.
- `DOCUMENT_VIEW` stays with the existing document layer.
- **`build_scope_query` is never widened**, and the generic scope service is
  never modified for Contract Master. Organisation tier is expressed as an
  explicit capability precisely because the generic layer cannot distinguish an
  organisation-tier catalogue actor from a project-tier one.
- The gate answers **membership**; row visibility is answered separately. A
  scope refusal keeps its own status — a masked 401 would force a client logout
  where a 403 should have shown a message.
