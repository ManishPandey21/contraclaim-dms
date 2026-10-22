import React, { Suspense } from "react";
import { Routes, Route } from "react-router-dom";
import { lazyWithRetry } from "@/lib/lazyWithRetry";
import MainLayout from "./components/layout/MainLayout";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import RouteSkeleton from "./components/layout/RouteSkeleton";
import RoleGuard from "./components/auth/RoleGuard";

// Lazy-loaded pages
const Dashboard = lazyWithRetry(() => import("./pages/Dashboard"));
const Overview = lazyWithRetry(() => import("./pages/Overview"));
const OrganizationsPage = lazyWithRetry(() => import("./pages/OrganizationsPage"));
const ProjectsPage = lazyWithRetry(() => import("./pages/ProjectsPage"));
const DocumentsPage = lazyWithRetry(() => import("./pages/DocumentsPage"));
const EnhancedDocumentsPage = lazyWithRetry(
  () => import("./pages/EnhancedDocumentsPage"),
);
const TagsPage = lazyWithRetry(() => import("./pages/TagsPage"));
const ProfilePage = lazyWithRetry(() =>
  import("./pages/ProfilePage").then((m) => ({ default: m.default })),
);
const UsersPage = lazyWithRetry(() => import("./pages/UsersPage"));
const PermissionsPage = lazyWithRetry(() => import("./pages/PermissionsPage"));
const SettingsPage = lazyWithRetry(() => import("./pages/SettingsPage"));
const PlanSettingsPage = lazyWithRetry(() => import("./pages/PlanSettingsPage"));
const SubscriptionManagementPage = lazyWithRetry(
  () => import("./pages/SubscriptionManagementPage"),
);
const BillingReturnPage = lazyWithRetry(() => import("./pages/BillingReturnPage"));
const NotificationCenterPage = lazyWithRetry(
  () => import("./pages/NotificationCenterPage"),
);
const UploadPage = lazyWithRetry(() => import("./pages/UploadPage"));
const DocumentViewerPage = lazyWithRetry(() => import("./pages/DocumentViewerPage"));
const RegisterPage = lazyWithRetry(() => import("./pages/RegisterPage"));
const FolderStructurePage = lazyWithRetry(() => import("./pages/FolderStructurePage"));
const TasksPage = lazyWithRetry(() => import("./pages/TasksPage"));
const PartiesInvolvedPage = lazyWithRetry(() => import("./pages/PartiesInvolvedPage"));
const LoginPage = lazyWithRetry(() => import("./pages/LoginPage"));
const SecurityTermsPage = lazyWithRetry(() => import("./pages/SecurityTermsPage"));
const LetterWorkflowPage = lazyWithRetry(() => import("./pages/LetterWorkflowPage"));
const LetterInputPage = lazyWithRetry(() => import("./pages/LetterInputPage"));
const LetterStrategicPlanPage = lazyWithRetry(
  () => import("./pages/LetterStrategicPlanPage"),
);
const LetterDraftPage = lazyWithRetry(() => import("./pages/LetterDraftPage"));
const LetterReviewPage = lazyWithRetry(() => import("./pages/LetterReviewPage"));
const LetterApprovalPage = lazyWithRetry(() => import("./pages/LetterApprovalPage"));
const LetterCompletedPage = lazyWithRetry(() => import("./pages/LetterCompletedPage"));
const LetterQualityDashboardPage = lazyWithRetry(
  () => import("./pages/LetterQualityDashboardPage"),
);
const LetterSummaryPage = lazyWithRetry(() => import("./pages/LetterSummaryPage"));
const ReportsAnalyticsPage = lazyWithRetry(() => import("./pages/ReportsAnalyticsPage"));
const ClaimsRegisterPage = lazyWithRetry(() => import("./pages/ClaimsRegisterPage"));
const ClaimDetailPage = lazyWithRetry(() => import("./pages/ClaimDetailPage"));
const SLATrackerPage = lazyWithRetry(() => import("./pages/SLATrackerPage"));
const KeyDateRegisterPage = lazyWithRetry(() => import("./pages/KeyDateRegisterPage"));
const KeyDateDetailPage = lazyWithRetry(() => import("./pages/KeyDateDetailPage"));
const VariationRegisterPage = lazyWithRetry(() => import("./pages/VariationRegisterPage"));
const BankGuaranteeRegisterPage = lazyWithRetry(() => import("./pages/BankGuaranteeRegisterPage"));
const InsuranceRegisterPage = lazyWithRetry(() => import("./pages/InsuranceRegisterPage"));
const ConcernsPage = lazyWithRetry(() => import("./pages/ConcernsPage"));
const BillingCatalogPage = lazyWithRetry(() => import("./pages/BillingCatalogPage"));
const RetrievalConsolePage = lazyWithRetry(() => import("./pages/RetrievalConsolePage"));
const IPCBillRegisterPage = lazyWithRetry(() => import("./pages/IPCBillRegisterPage"));
const ObservabilityPage = lazyWithRetry(() => import("./pages/ObservabilityPage"));
const LetterTemplatePage = lazyWithRetry(() => import("./pages/LetterTemplatePage"));
const LetterTemplateEditorPage = lazyWithRetry(
  () => import("./pages/LetterTemplateEditorPage"),
);
const RepresentativesPage = lazyWithRetry(() => import("./pages/RepresentativesPage"));
const ContractsPage = lazyWithRetry(() => import("./pages/ContractsPage"));
const ContractsUploadPage = lazyWithRetry(() => import("./pages/ContractsUploadPage"));
const ContractsSearchPage = lazyWithRetry(() => import("./pages/ContractsSearchPage"));
const ContractQAPage = lazyWithRetry(() => import("./pages/ContractQAPage"));
const ContractViewerPage = lazyWithRetry(() => import("./pages/ContractViewerPage"));
const ContractAppraisalPage = lazyWithRetry(() => import("./pages/ContractAppraisalPage"));
const ContractMasterPage = lazyWithRetry(() => import("./pages/ContractMasterPage"));
const ContractTimelinePage = lazyWithRetry(() => import("./pages/ContractTimelinePage"));
const LegalWordsPage = lazyWithRetry(() => import("./pages/LegalWordsPage"));
const AdminLegalWordsPage = lazyWithRetry(() => import("./pages/AdminLegalWordsPage"));
const ArbitrationCaseWorkspacePage = lazyWithRetry(() => import("./pages/ArbitrationCaseWorkspacePage"));
const ArbitrationDraftingPage = lazyWithRetry(() => import("./pages/ArbitrationDraftingPage"));
const ChronologyBuilderPage = lazyWithRetry(() => import("./pages/ChronologyBuilderPage"));
const ReferencePage = lazyWithRetry(() => import("./pages/ReferencePage"));
const ShareDocumentPage = lazyWithRetry(() => import("./pages/ShareDocumentPage"));
const EmailGroupsPage = lazyWithRetry(() => import("./pages/EmailGroupsPage"));
const NotFound = lazyWithRetry(() => import("./pages/NotFound"));
const HealthPage = lazyWithRetry(() => import("./pages/HealthPage"));
const LandingPage = lazyWithRetry(() => import("./pages/LandingPage"));
const BlogPage = lazyWithRetry(() => import("./pages/BlogPage"));
const BlogArticlePage = lazyWithRetry(() => import("./pages/BlogArticlePage"));
const BlogVideoPage = lazyWithRetry(() => import("./pages/BlogVideoPage"));
const BlogNotFound = lazyWithRetry(() => import("./pages/BlogNotFound"));

const AppRoutes = () => (
  <Suspense fallback={<RouteSkeleton />}>
    <Routes>
      <Route
        path="/"
        element={<LandingPage />}
      />
      {/* Public blog. Sits outside ProtectedRoute on purpose: these pages are
          static editorial content and must be reachable without a session. */}
      <Route path="/blog" element={<BlogPage />} />
      <Route path="/blog/articles/:slug" element={<BlogArticlePage />} />
      <Route path="/blog/videos/:slug" element={<BlogVideoPage />} />
      <Route path="/blog/*" element={<BlogNotFound />} />
      <Route
        path="/security-terms"
        element={
          <ProtectedRoute>
            <SecurityTermsPage />
          </ProtectedRoute>
        }
      />
      <Route
        element={
          <ProtectedRoute>
            <MainLayout />
          </ProtectedRoute>
        }
      >
        <Route path="overview" element={<Overview />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="organizations" element={<OrganizationsPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documentsearch" element={<EnhancedDocumentsPage />} />
        <Route path="tags" element={<TagsPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="permissions" element={<PermissionsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="plan-settings" element={<PlanSettingsPage />} />
        <Route
          path="subscription-management"
          element={
            <RoleGuard path="/subscription-management" fallback="/overview">
              <SubscriptionManagementPage />
            </RoleGuard>
          }
        />
        <Route path="notifications" element={<NotificationCenterPage />} />
        <Route path="legal-words" element={<LegalWordsPage />} />
        <Route
          path="billing/return"
          element={
            <RoleGuard path="/billing/return" fallback="/overview">
              <BillingReturnPage />
            </RoleGuard>
          }
        />
        <Route path="upload" element={<UploadPage />} />
        <Route path="/documentviewer/:id" element={<DocumentViewerPage />} />
        <Route path="share/:id" element={<ShareDocumentPage />} />
        <Route path="email-groups" element={<EmailGroupsPage />} />
        <Route
          path="register"
          element={
            <RoleGuard path="/register" fallback="/overview">
              <RegisterPage />
            </RoleGuard>
          }
        />
        <Route path="folders" element={<FolderStructurePage />} />
        <Route
          path="tasks"
          element={
            <RoleGuard path="/tasks" fallback="/overview">
              <TasksPage />
            </RoleGuard>
          }
        />
        <Route path="parties" element={<PartiesInvolvedPage />} />
        <Route
          path="letters"
          element={
            <RoleGuard path="/letters" fallback="/overview">
              <LetterWorkflowPage />
            </RoleGuard>
          }
        />
        <Route path="documents/summary/:id" element={<LetterSummaryPage />} />
        <Route path="letters/:id/input" element={<LetterInputPage />} />
        <Route
          path="letters/:id/strategic-plan"
          element={<LetterStrategicPlanPage />}
        />
        <Route
          path="letters/:id/strategy"
          element={<LetterStrategicPlanPage />}
        />
        <Route path="letters/:id/draft" element={<LetterDraftPage />} />
        <Route path="letters/:id/review" element={<LetterReviewPage />} />
        <Route path="letters/:id/approval" element={<LetterApprovalPage />} />
        <Route path="letters/:id/completed" element={<LetterCompletedPage />} />
        <Route path="letter-quality" element={<LetterQualityDashboardPage />} />
        <Route path="reports" element={<ReportsAnalyticsPage />} />
        <Route
          path="claims"
          element={
            <RoleGuard path="/claims" fallback="/overview">
              <ClaimsRegisterPage />
            </RoleGuard>
          }
        />
        <Route
          path="claims/:id"
          element={
            <RoleGuard path="/claims" fallback="/overview">
              <ClaimDetailPage />
            </RoleGuard>
          }
        />
        <Route
          path="sla"
          element={
            <RoleGuard path="/sla" fallback="/overview">
              <SLATrackerPage />
            </RoleGuard>
          }
        />
        <Route
          path="key-dates"
          element={
            <RoleGuard path="/key-dates" fallback="/overview">
              <KeyDateRegisterPage />
            </RoleGuard>
          }
        />
        <Route
          path="key-dates/:id"
          element={
            <RoleGuard path="/key-dates" fallback="/overview">
              <KeyDateDetailPage />
            </RoleGuard>
          }
        />
        <Route
          path="variations"
          element={
            <RoleGuard path="/variations" fallback="/overview">
              <VariationRegisterPage />
            </RoleGuard>
          }
        />
        <Route
          path="bank-guarantees"
          element={
            <RoleGuard path="/bank-guarantees" fallback="/overview">
              <BankGuaranteeRegisterPage />
            </RoleGuard>
          }
        />
        <Route
          path="insurance"
          element={
            <RoleGuard path="/insurance" fallback="/overview">
              <InsuranceRegisterPage />
            </RoleGuard>
          }
        />
        <Route
          path="ipc-bills"
          element={
            <RoleGuard path="/ipc-bills" fallback="/overview">
              <IPCBillRegisterPage />
            </RoleGuard>
          }
        />
        <Route
          path="concerns"
          element={
            <RoleGuard path="/concerns" fallback="/overview">
              <ConcernsPage />
            </RoleGuard>
          }
        />
        <Route
          path="admin/billing-catalog"
          element={
            <RoleGuard path="/admin/billing-catalog" fallback="/overview">
              <BillingCatalogPage />
            </RoleGuard>
          }
        />
        <Route
          path="retrieval-console"
          element={
            <RoleGuard path="/retrieval-console" fallback="/overview">
              <RetrievalConsolePage />
            </RoleGuard>
          }
        />
        <Route
          path="observability"
          element={
            <RoleGuard path="/observability" fallback="/overview">
              <ObservabilityPage />
            </RoleGuard>
          }
        />
        <Route
          path="admin/legal-words"
          element={
            <RoleGuard path="/admin/legal-words" fallback="/overview">
              <AdminLegalWordsPage />
            </RoleGuard>
          }
        />
        <Route path="letter-templates" element={<LetterTemplatePage />} />
        <Route
          path="letter-templates/:id/edit"
          element={<LetterTemplateEditorPage />}
        />
        <Route path="representatives" element={<RepresentativesPage />} />
        <Route path="contracts" element={<ContractsPage />} />
        <Route path="contracts/upload" element={<ContractsUploadPage />} />
        <Route path="contracts/search" element={<ContractsSearchPage />} />
        <Route path="contracts/qa" element={<ContractQAPage />} />
        <Route path="contracts/viewer" element={<ContractViewerPage />} />
        <Route path="contracts/viewer/:id" element={<ContractViewerPage />} />
        <Route path="contracts/clauses" element={<ContractViewerPage />} />
        <Route
          path="contracts/timeline"
          element={
            <RoleGuard path="/contracts/timeline" fallback="/overview">
              <ContractTimelinePage />
            </RoleGuard>
          }
        />
        <Route
          path="chronology"
          element={
            <RoleGuard path="/chronology" fallback="/overview">
              <ChronologyBuilderPage />
            </RoleGuard>
          }
        />
        <Route
          path="chronology/new"
          element={
            <RoleGuard path="/chronology/new" fallback="/overview">
              <ChronologyBuilderPage />
            </RoleGuard>
          }
        />
        <Route
          path="chronology/:chronologyId"
          element={
            <RoleGuard path="/chronology" fallback="/overview">
              <ChronologyBuilderPage />
            </RoleGuard>
          }
        />
        <Route
          path="chronology/:chronologyId/review"
          element={
            <RoleGuard path="/chronology" fallback="/overview">
              <ChronologyBuilderPage />
            </RoleGuard>
          }
        />
        <Route
          path="chronology/:chronologyId/presentation"
          element={
            <RoleGuard path="/chronology" fallback="/overview">
              <ChronologyBuilderPage />
            </RoleGuard>
          }
        />
        <Route
          path="contracts/appraisal"
          element={
            <RoleGuard path="/contracts/appraisal" fallback="/overview">
              <ContractAppraisalPage />
            </RoleGuard>
          }
        />
        <Route
          path="contracts/master"
          element={
            <RoleGuard path="/contracts/master" fallback="/overview">
              <ContractMasterPage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration"
          element={
            <RoleGuard path="/arbitration" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/cases"
          element={
            <RoleGuard path="/arbitration/cases" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/cases/new"
          element={
            <RoleGuard path="/arbitration/cases/new" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/cases/:caseId"
          element={
            <RoleGuard path="/arbitration/cases" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/cases/:caseId/matrices"
          element={
            <RoleGuard path="/arbitration/cases" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/cases/:caseId/readiness"
          element={
            <RoleGuard path="/arbitration/cases" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/cases/:caseId/filing-bundle"
          element={
            <RoleGuard path="/arbitration/cases" fallback="/overview">
              <ArbitrationCaseWorkspacePage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/claim"
          element={
            <RoleGuard path="/arbitration/claim" fallback="/overview">
              <ArbitrationDraftingPage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/defence"
          element={
            <RoleGuard path="/arbitration/defence" fallback="/overview">
              <ArbitrationDraftingPage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/rejoinder"
          element={
            <RoleGuard path="/arbitration/rejoinder" fallback="/overview">
              <ArbitrationDraftingPage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/counterclaim"
          element={
            <RoleGuard path="/arbitration/counterclaim" fallback="/overview">
              <ArbitrationDraftingPage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/drafts"
          element={
            <RoleGuard path="/arbitration/drafts" fallback="/overview">
              <ArbitrationDraftingPage />
            </RoleGuard>
          }
        />
        <Route
          path="arbitration/drafts/:draftId"
          element={
            <RoleGuard path="/arbitration/drafts" fallback="/overview">
              <ArbitrationDraftingPage />
            </RoleGuard>
          }
        />
        <Route path="reference/:id" element={<ReferencePage />} />
        <Route
          path="health"
          element={
            <RoleGuard path="/health" fallback="/overview">
              <HealthPage />
            </RoleGuard>
          }
        />
      </Route>

      <Route path="/login" element={<LoginPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  </Suspense>
);

export default AppRoutes;
