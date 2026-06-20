import React, { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import MainLayout from "./components/layout/MainLayout";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import RouteSkeleton from "./components/layout/RouteSkeleton";
import RoleGuard from "./components/auth/RoleGuard";

// Lazy-loaded pages
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Overview = lazy(() => import("./pages/Overview"));
const OrganizationsPage = lazy(() => import("./pages/OrganizationsPage"));
const ProjectsPage = lazy(() => import("./pages/ProjectsPage"));
const DocumentsPage = lazy(() => import("./pages/DocumentsPage"));
const EnhancedDocumentsPage = lazy(
  () => import("./pages/EnhancedDocumentsPage"),
);
const TagsPage = lazy(() => import("./pages/TagsPage"));
const ProfilePage = lazy(() =>
  import("./pages/ProfilePage").then((m) => ({ default: m.default })),
);
const UsersPage = lazy(() => import("./pages/UsersPage"));
const PermissionsPage = lazy(() => import("./pages/PermissionsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const PlanSettingsPage = lazy(() => import("./pages/PlanSettingsPage"));
const SubscriptionManagementPage = lazy(
  () => import("./pages/SubscriptionManagementPage"),
);
const BillingReturnPage = lazy(() => import("./pages/BillingReturnPage"));
const NotificationCenterPage = lazy(
  () => import("./pages/NotificationCenterPage"),
);
const UploadPage = lazy(() => import("./pages/UploadPage"));
const DocumentViewerPage = lazy(() => import("./pages/DocumentViewerPage"));
const RegisterPage = lazy(() => import("./pages/RegisterPage"));
const FolderStructurePage = lazy(() => import("./pages/FolderStructurePage"));
const TasksPage = lazy(() => import("./pages/TasksPage"));
const PartiesInvolvedPage = lazy(() => import("./pages/PartiesInvolvedPage"));
const LoginPage = lazy(() => import("./pages/LoginPage"));
const LetterWorkflowPage = lazy(() => import("./pages/LetterWorkflowPage"));
const LetterInputPage = lazy(() => import("./pages/LetterInputPage"));
const LetterStrategicPlanPage = lazy(
  () => import("./pages/LetterStrategicPlanPage"),
);
const LetterDraftPage = lazy(() => import("./pages/LetterDraftPage"));
const LetterReviewPage = lazy(() => import("./pages/LetterReviewPage"));
const LetterApprovalPage = lazy(() => import("./pages/LetterApprovalPage"));
const LetterCompletedPage = lazy(() => import("./pages/LetterCompletedPage"));
const LetterQualityDashboardPage = lazy(
  () => import("./pages/LetterQualityDashboardPage"),
);
const LetterSummaryPage = lazy(() => import("./pages/LetterSummaryPage"));
const ReportsAnalyticsPage = lazy(() => import("./pages/ReportsAnalyticsPage"));
const ClaimsRegisterPage = lazy(() => import("./pages/ClaimsRegisterPage"));
const ClaimDetailPage = lazy(() => import("./pages/ClaimDetailPage"));
const SLATrackerPage = lazy(() => import("./pages/SLATrackerPage"));
const KeyDateRegisterPage = lazy(() => import("./pages/KeyDateRegisterPage"));
const KeyDateDetailPage = lazy(() => import("./pages/KeyDateDetailPage"));
const VariationRegisterPage = lazy(() => import("./pages/VariationRegisterPage"));
const BankGuaranteeRegisterPage = lazy(() => import("./pages/BankGuaranteeRegisterPage"));
const LetterTemplatePage = lazy(() => import("./pages/LetterTemplatePage"));
const LetterTemplateEditorPage = lazy(
  () => import("./pages/LetterTemplateEditorPage"),
);
const RepresentativesPage = lazy(() => import("./pages/RepresentativesPage"));
const ContractsPage = lazy(() => import("./pages/ContractsPage"));
const ContractsUploadPage = lazy(() => import("./pages/ContractsUploadPage"));
const ContractsSearchPage = lazy(() => import("./pages/ContractsSearchPage"));
const ContractQAPage = lazy(() => import("./pages/ContractQAPage"));
const ContractAppraisalPage = lazy(() => import("./pages/ContractAppraisalPage"));
const ReferencePage = lazy(() => import("./pages/ReferencePage"));
const ShareDocumentPage = lazy(() => import("./pages/ShareDocumentPage"));
const EmailGroupsPage = lazy(() => import("./pages/EmailGroupsPage"));
const NotFound = lazy(() => import("./pages/NotFound"));
const HealthPage = lazy(() => import("./pages/HealthPage"));
const LandingPage = lazy(() => import("./pages/LandingPage"));

const AppRoutes = () => (
  <Suspense fallback={<RouteSkeleton />}>
    <Routes>
      <Route
        path="/"
        element={<LandingPage />}
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
        <Route
          path="contracts/appraisal"
          element={
            <RoleGuard path="/contracts/appraisal" fallback="/overview">
              <ContractAppraisalPage />
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
