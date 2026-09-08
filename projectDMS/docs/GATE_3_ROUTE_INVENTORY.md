# Gate 3 production route inventory

**Generated. Do not edit by hand.** Regenerate with:

```bash
python scripts/gate3_route_inventory.py --format markdown > docs/GATE_3_ROUTE_INVENTORY.md
```

This file is the denominator for Gate 3 bullet 8. The bullet used to say "every
production route" and name no route set, which made it unmeasurable in both
directions - it could never go green, and nobody could say which route had been
missed. The route list below is parsed out of `client/src/routes.tsx`, the
registration React Router actually uses, so it cannot drift from the application
without `backend/rbac_backend/tests/test_gate3_route_inventory.py` failing.

**What is derived and what is declared.** `ROUTE`, `METHOD`, `AUTH REQUIRED` and
`UI PAGE` come from the router source. `EMPTY`, `LOADING` and `ERROR TEST
LOCATION` are declared per page component in
`scripts/gate3_route_inventory.py::PAGE_STATES`, because whether a page owes a
deliberate empty state is a product judgement and this repository has been
burned before by substring heuristics that claimed a property they could not
see. The guard enforces completeness, not correctness: a page with no
declaration fails the build, and a declaration naming no page fails it too.

`METHOD` is `GET` throughout: these are browser navigations, and the states this
bullet is about are the ones a viewer sees on arrival.

**ERROR TEST LOCATION** is where deliberate-failure behaviour is proven. Gate 3
no longer certifies it - an API failure induced on demand requires fault
injection, and a run that injects faults is not the unmocked staging run Gate 3
requires - so the requirement lives at the component and mocked-E2E layer, where
controlled failure is legitimate. `MISSING` is an open test debt, tracked by
Gate 3's execution plan; it is not a licence to ship a page with no error state.


## Counts

- Source: `client/src/routes.tsx`
- Routes: **92**
- Authenticated routes: **85**
- Routes owing an empty state: **59**
- Routes owing a loading state: **85**

## The inventory

| ROUTE | METHOD | AUTH REQUIRED | UI PAGE | EMPTY | LOADING | ERROR TEST LOCATION |
|---|---|---|---|---|---|---|
| `/` | GET | no | `LandingPage` | no | no | n/a |
| `/*` | GET | no | `NotFound` | no | no | n/a |
| `/admin/billing-catalog` | GET | yes | `BillingCatalogPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/admin/legal-words` | GET | yes | `AdminLegalWordsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/arbitration` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/cases` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/cases/:caseId` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/cases/:caseId/filing-bundle` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/cases/:caseId/matrices` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/cases/:caseId/readiness` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/cases/new` | GET | yes | `ArbitrationCaseWorkspacePage` | yes | yes | `client/src/pages/__tests__/ArbitrationCaseWorkspacePage.test.tsx` |
| `/arbitration/claim` | GET | yes | `ArbitrationDraftingPage` | yes | yes | `client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx` |
| `/arbitration/counterclaim` | GET | yes | `ArbitrationDraftingPage` | yes | yes | `client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx` |
| `/arbitration/defence` | GET | yes | `ArbitrationDraftingPage` | yes | yes | `client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx` |
| `/arbitration/drafts` | GET | yes | `ArbitrationDraftingPage` | yes | yes | `client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx` |
| `/arbitration/drafts/:draftId` | GET | yes | `ArbitrationDraftingPage` | yes | yes | `client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx` |
| `/arbitration/rejoinder` | GET | yes | `ArbitrationDraftingPage` | yes | yes | `client/src/pages/__tests__/ArbitrationDraftingPage.test.tsx` |
| `/bank-guarantees` | GET | yes | `BankGuaranteeRegisterPage` | yes | yes | `client/src/pages/__tests__/BankGuaranteeRegisterPage.event-evidence.test.tsx` |
| `/billing/return` | GET | yes | `BillingReturnPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/blog` | GET | no | `BlogPage` | no | no | n/a |
| `/blog/*` | GET | no | `BlogNotFound` | no | no | n/a |
| `/blog/articles/:slug` | GET | no | `BlogArticlePage` | no | no | `client/src/pages/__tests__/BlogArticlePage.test.tsx` |
| `/blog/videos/:slug` | GET | no | `BlogVideoPage` | no | no | `client/src/pages/__tests__/BlogVideoPage.test.tsx` |
| `/chronology` | GET | yes | `ChronologyBuilderPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/chronology/:chronologyId` | GET | yes | `ChronologyBuilderPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/chronology/:chronologyId/presentation` | GET | yes | `ChronologyBuilderPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/chronology/:chronologyId/review` | GET | yes | `ChronologyBuilderPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/chronology/new` | GET | yes | `ChronologyBuilderPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/claims` | GET | yes | `ClaimsRegisterPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/claims/:id` | GET | yes | `ClaimDetailPage` | no | yes | `client/src/pages/__tests__/ClaimDetailPage.document-links.test.tsx` |
| `/concerns` | GET | yes | `ConcernsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/contract-master/prototype` | GET | yes | `ContractMasterPrototypePage` | no | yes | `client/src/pages/__tests__/ContractMasterPrototypePage.test.tsx` |
| `/contract-master/workspace` | GET | yes | `ContractMasterWorkspacePage` | yes | yes | `client/e2e/contract-master.spec.ts` |
| `/contracts` | GET | yes | `ContractsPage` | yes | yes | `client/e2e/contract-workflows.spec.ts` |
| `/contracts/appraisal` | GET | yes | `ContractAppraisalPage` | yes | yes | `client/e2e/contract-workflows.spec.ts` |
| `/contracts/clauses` | GET | yes | `ContractViewerPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/contracts/master` | GET | yes | `ContractMasterPage` | yes | yes | `client/e2e/contract-master.spec.ts` |
| `/contracts/qa` | GET | yes | `ContractQAPage` | yes | yes | `client/e2e/contract-workflows.spec.ts` |
| `/contracts/search` | GET | yes | `ContractsSearchPage` | yes | yes | `client/e2e/contract-workflows.spec.ts` |
| `/contracts/timeline` | GET | yes | `ContractTimelinePage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/contracts/upload` | GET | yes | `ContractsUploadPage` | no | yes | `client/e2e/contract-workflows.spec.ts` |
| `/contracts/viewer` | GET | yes | `ContractViewerPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/contracts/viewer/:id` | GET | yes | `ContractViewerPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/dashboard` | GET | yes | `Dashboard` | no | yes | `client/src/pages/__tests__/Dashboard.test.tsx` |
| `/documents` | GET | yes | `DocumentsPage` | yes | yes | `client/src/pages/__tests__/DocumentsPage.under-process-guard.test.tsx` |
| `/documents/summary/:id` | GET | yes | `LetterSummaryPage` | no | yes | `client/src/pages/__tests__/LetterSummaryPage.test.tsx` |
| `/documentsearch` | GET | yes | `EnhancedDocumentsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/documentviewer/:id` | GET | yes | `DocumentViewerPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/email-groups` | GET | yes | `EmailGroupsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/folders` | GET | yes | `FolderStructurePage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/health` | GET | yes | `HealthPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/insurance` | GET | yes | `InsuranceRegisterPage` | yes | yes | `client/src/pages/__tests__/InsuranceRegisterPage.document-links.test.tsx` |
| `/ipc-bills` | GET | yes | `IPCBillRegisterPage` | yes | yes | `client/src/pages/__tests__/IPCBillRegisterPage.document-links.test.tsx` |
| `/key-dates` | GET | yes | `KeyDateRegisterPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/key-dates/:id` | GET | yes | `KeyDateDetailPage` | no | yes | `client/src/pages/__tests__/KeyDateDetailPage.achievement-evidence.test.tsx` |
| `/legal-words` | GET | yes | `LegalWordsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/letter-quality` | GET | yes | `LetterQualityDashboardPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/letter-templates` | GET | yes | `LetterTemplatePage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/letter-templates/:id/edit` | GET | yes | `LetterTemplateEditorPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/letters` | GET | yes | `LetterWorkflowPage` | yes | yes | `client/src/pages/__tests__/LetterWorkflowPage.integration.test.tsx` |
| `/letters/:id/approval` | GET | yes | `LetterApprovalPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/letters/:id/completed` | GET | yes | `LetterCompletedPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/letters/:id/draft` | GET | yes | `LetterDraftPage` | no | yes | `client/src/pages/__tests__/LetterDraftPage.basic-render.test.tsx` |
| `/letters/:id/input` | GET | yes | `LetterInputPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/letters/:id/review` | GET | yes | `LetterReviewPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/letters/:id/strategic-plan` | GET | yes | `LetterStrategicPlanPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/letters/:id/strategy` | GET | yes | `LetterStrategicPlanPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/login` | GET | no | `LoginPage` | no | yes | `client/src/pages/__tests__/LoginPage.login.test.tsx` |
| `/notifications` | GET | yes | `NotificationCenterPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/observability` | GET | yes | `ObservabilityPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/organizations` | GET | yes | `OrganizationsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/overview` | GET | yes | `Overview` | no | no | n/a |
| `/parties` | GET | yes | `PartiesInvolvedPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/permissions` | GET | yes | `PermissionsPage` | yes | yes | `client/src/pages/__tests__/PermissionsPage.permission-catalog.test.ts` |
| `/plan-settings` | GET | yes | `PlanSettingsPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/profile` | GET | yes | `ProfilePage` | no | yes | MISSING - no fault-injection coverage yet |
| `/projects` | GET | yes | `ProjectsPage` | yes | yes | `client/src/pages/__tests__/ProjectsPage.organization-filter.test.tsx` |
| `/reference/:id` | GET | yes | `ReferencePage` | no | yes | `client/src/pages/__tests__/ReferencePage.parsed-references.test.tsx` |
| `/register` | GET | yes | `RegisterPage` | no | yes | `client/src/pages/__tests__/RegisterPage.subscription.test.tsx` |
| `/reports` | GET | yes | `ReportsAnalyticsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/representatives` | GET | yes | `RepresentativesPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/retrieval-console` | GET | yes | `RetrievalConsolePage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/security-terms` | GET | yes | `SecurityTermsPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/settings` | GET | yes | `SettingsPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/share/:id` | GET | yes | `ShareDocumentPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/sla` | GET | yes | `SLATrackerPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/subscription-management` | GET | yes | `SubscriptionManagementPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/tags` | GET | yes | `TagsPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/tasks` | GET | yes | `TasksPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/upload` | GET | yes | `UploadPage` | no | yes | MISSING - no fault-injection coverage yet |
| `/users` | GET | yes | `UsersPage` | yes | yes | MISSING - no fault-injection coverage yet |
| `/variations` | GET | yes | `VariationRegisterPage` | yes | yes | MISSING - no fault-injection coverage yet |

