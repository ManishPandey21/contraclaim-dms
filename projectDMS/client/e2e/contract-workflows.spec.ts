import { expect, Page, Route, test } from "@playwright/test";

const org = { id: "org-A", _id: "org-A", name: "Aurora Engineering" };
const project = { id: "proj-A", _id: "proj-A", name: "Metro Rail Package 01", organization_id: org.id };
const contractUpload = {
  document_id: "doc-gcc",
  upload_id: "upload-gcc",
  filename: "GCC General Conditions.pdf",
  status: "completed",
  categories: ["GCC"],
  createdAt: "2026-01-10T10:00:00Z",
  size: 524288,
};

const report = {
  _id: "report-1",
  organization_id: org.id,
  project_id: project.id,
  job_id: "job-1",
  document_ids: ["doc-gcc"],
  report_version: 1,
  status: "draft",
  document_completeness_status: "complete",
  missing_documents: [],
  executive_summary: "The contract contains extension of time, payment, and notice obligations.",
  full_report_markdown: "## Contract Appraisal\n\nThe contract is supported by cited clauses.",
  sections: [
    {
      key: "time",
      title: "Time for Completion",
      markdown: "GCC 8.4 governs extension of time and requires notice.",
      citations: [
        {
          document_id: "doc-gcc",
          chunk_id: "gcc-8.4",
          clause_number: "8.4",
          clause_title: "Extension of Time",
          page: 42,
          page_numbers: [42],
          snippet: "The Contractor shall be entitled to extension of time.",
        },
      ],
      supported: true,
      confidence: 0.92,
    },
  ],
  citations: [],
  overall_risk_rating: "medium",
  confidence_score: 0.92,
  review_comments_count: 0,
  is_locked: false,
  created_at: "2026-01-10T10:05:00Z",
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockContractApi(page: Page) {
  let appraisalGenerated = false;
  let uploadCompleted = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "") || "/";
    const method = request.method();

    if (path === "/csrf-token") {
      return json(route, { csrf_token: "test-csrf" });
    }
    if (path === "/me") {
      return json(route, {
        id: "user-1",
        email: "superadmin@example.com",
        roles: ["superadmin"],
        organization_id: org.id,
        projects: [project.id],
      });
    }
    if (path === "/roles") {
      return json(route, []);
    }
    if (path === "/organizations") {
      return json(route, { organizations: [org] });
    }
    if (path === "/projects") {
      return json(route, [project]);
    }
    if (path === "/contracts/list") {
      const uploads = uploadCompleted
        ? [
            {
              ...contractUpload,
              document_id: "doc-uploaded",
              upload_id: "upload-new",
              filename: "EOT Conditions.pdf",
              createdAt: "2026-01-11T10:00:00Z",
            },
            contractUpload,
          ]
        : [contractUpload];
      return json(route, { uploads, count: uploads.length });
    }
    if (path === "/contracts/upload-session" && method === "POST") {
      return json(route, {
        upload_id: "upload-new",
        organization_id: org.id,
        project_id: project.id,
        expires_at: "2026-01-11T11:00:00Z",
        max_file_size_bytes: 104857600,
        max_chunk_size_bytes: 5242880,
        max_chunks: 200,
        allowed_extensions: [".pdf", ".docx"],
        allowed_mime_types: ["application/pdf"],
      });
    }
    if (path === "/contracts/upload-multipart" && method === "POST") {
      uploadCompleted = true;
      return json(route, {
        organization_id: org.id,
        project_id: project.id,
        results: [
          {
            upload_id: "upload-new",
            document_id: "doc-uploaded",
            filename: "EOT Conditions.pdf",
            status: "queued",
          },
        ],
      });
    }
    if (path === "/contracts/status") {
      return json(route, {
        upload_id: "upload-new",
        document_id: "doc-uploaded",
        status: "completed",
        organization_id: org.id,
        project_id: project.id,
        categories: ["GCC", "EOT"],
        progress: 100,
        stage_label: "Processing complete",
      });
    }
    if (path === "/contracts/search" && method === "POST") {
      const post = request.postDataJSON() as { query?: string };
      const query = String(post?.query || "").toLowerCase();
      if (query.includes("server error")) {
        return json(route, { detail: "Search service unavailable" }, 503);
      }
      if (query.includes("no result")) {
        return json(route, {
          results: [],
          total_count: 0,
          has_more: false,
          current_page: 1,
          page_size: 20,
          sources: [],
        });
      }
      return json(route, {
        results: [
          {
            upload_id: "upload-gcc",
            document_id: "doc-gcc",
            file_name: "GCC General Conditions.pdf",
            source_filename: "GCC General Conditions.pdf",
            chunk_index: 0,
            clause_number: "8.4",
            clause_title: "Extension of Time",
            clause_type: "clause",
            clause_level: 2,
            parent_clause_number: "8",
            is_complete_clause: true,
            clause_start_position: 1200,
            clause_end_position: 1600,
            text: "GCC 8.4 entitles the Contractor to extension of time for Employer delay.",
            score: 0.91,
            page_number: 42,
            page: 42,
            page_numbers: [42],
            section_heading: "General Conditions",
            clause_tags: ["GCC 8.4", "EOT"],
          },
        ],
        summary: "GCC 8.4 is the top match for extension of time.",
        ai_summary_title: "Extension of Time Summary",
        total_count: 1,
        has_more: false,
        current_page: 1,
        page_size: 20,
        sources: [
          {
            document_id: "doc-gcc",
            upload_id: "upload-gcc",
            file_name: "GCC General Conditions.pdf",
            clause_number: "8.4",
            clause_title: "Extension of Time",
            section_heading: "General Conditions",
            page_numbers: [42],
            page_number: 42,
            page: 42,
            clause_tags: ["GCC 8.4", "EOT"],
          },
        ],
      });
    }
    if (path === "/v1/retrieval/contract-qa" && method === "POST") {
      return json(route, {
        answer: "GCC 8.4 permits extension of time for Employer delay when notice is given [GCC>8.4].",
        citations: [
          {
            document_id: "doc-gcc",
            chunk_id: "gcc-8.4",
            page: 42,
            score: 0.91,
            snippet: "The Contractor shall give notice for extension of time.",
            document_title: "GCC Contract",
            file_name: "GCC General Conditions.pdf",
            clause_number: "8.4",
            clause_title: "Extension of Time",
            section_heading: "General Conditions",
            page_numbers: [42],
          },
        ],
        strategy_used: "rag_fusion",
        timings: { total_ms: 50 },
        trace: [
          {
            iteration: 1,
            queries: ["GCC 8.4"],
            retrieved_ids: ["GCC>8.4"],
            critique: "sufficient",
            refinements: [],
          },
        ],
      });
    }
    if (path === "/contracts/appraisal/existing") {
      return json(route, appraisalGenerated ? report : null);
    }
    if (path === "/contracts/appraisal/generate" && method === "POST") {
      appraisalGenerated = true;
      return json(route, {
        _id: "job-1",
        organization_id: org.id,
        project_id: project.id,
        document_ids: ["doc-gcc"],
        status: "generating",
        current_step: "Generating appraisal",
        progress: 35,
        report_id: null,
      }, 202);
    }
    if (path === "/contracts/appraisal/jobs/job-1") {
      return json(route, {
        _id: "job-1",
        organization_id: org.id,
        project_id: project.id,
        document_ids: ["doc-gcc"],
        status: "completed",
        current_step: "Completed",
        progress: 100,
        report_id: "report-1",
      });
    }
    if (path === "/contracts/appraisal" && method === "GET") {
      return json(route, appraisalGenerated ? [report] : []);
    }
    if (path === "/contracts/appraisal/report-1") {
      return json(route, report);
    }
    if (
      path === "/contracts/obligations" ||
      path === "/contracts/risks" ||
      path === "/contracts/key-dates" ||
      path === "/contracts/clauses" ||
      path.endsWith("/review-comments")
    ) {
      return json(route, []);
    }

    return json(route, {});
  });
}

async function selectRadixOption(page: Page, testId: string, optionName: string) {
  await page.getByTestId(testId).click();
  await page.getByRole("option", { name: optionName }).click();
}

test.beforeEach(async ({ page }) => {
  await mockContractApi(page);
});

test("contract upload shows progress and completed ingestion state", async ({ page }) => {
  await page.goto("/contracts/upload");

  await expect(page.getByRole("heading", { name: "Upload Contracts" })).toBeVisible();
  await page.getByTestId("contract-upload-org-select").selectOption(org.id);
  await page.getByTestId("contract-upload-project-select").selectOption(project.id);
  await page.getByTestId("contract-upload-file-input").setInputFiles({
    name: "EOT Conditions.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 test contract"),
  });

  await expect(page.getByText("Selected Files (1)")).toBeVisible();
  await page.getByTestId("contract-upload-submit").click();

  const progress = page.getByTestId("contract-upload-progress");
  await expect(progress).toContainText("EOT Conditions.pdf");
  await expect(progress).toContainText("completed", { timeout: 10_000 });
  await expect(progress).toContainText("Processing complete");
  await expect(progress).toContainText("EOT");
});

test("contract search renders success, empty, and API-error states", async ({ page }) => {
  await page.goto("/contracts/search");

  await page.getByTestId("contract-search-org-select").selectOption(org.id);
  await page.getByTestId("contract-search-project-select").selectOption(project.id);
  await expect(page.getByTestId("contract-search-file-select")).toContainText("GCC General Conditions.pdf");

  await page.getByTestId("contract-search-query-input").fill("extension of time");
  await page.getByTestId("contract-search-submit").click();
  await expect(page.getByText("Clause 8.4")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Extension of Time" })).toBeVisible();
  await expect(page.getByText("Extension of Time Summary")).toBeVisible();

  await page.getByTestId("contract-search-query-input").fill("no result");
  await page.getByTestId("contract-search-submit").click();
  await expect(page.getByText("No clauses found")).toBeVisible();

  await page.getByTestId("contract-search-query-input").fill("server error");
  await page.getByTestId("contract-search-submit").click();
  await expect(page.getByText(/Search service unavailable|Search failed/i)).toBeVisible();
});

test("contract Q&A validates required scope and renders cited answer", async ({ page }) => {
  await page.goto("/contracts/qa");

  // CL-4A: the page's organisation/project follow the navbar selection, so the
  // first unmet requirement is the contract document.
  await expect(page.getByTestId("contract-qa-org-select")).toHaveValue(org.id);
  await expect(page.getByTestId("contract-qa-project-select")).toHaveValue(project.id);
  await page.getByTestId("contract-qa-submit").click();
  await expect(page.getByText("Select a contract document.")).toBeVisible();

  await page.getByTestId("contract-qa-file-select").selectOption("doc-gcc");
  await page.getByTestId("contract-qa-question-input").fill("What does GCC 8.4 say about extension of time?");
  await page.getByTestId("contract-qa-submit").click();

  const answer = page.getByTestId("contract-qa-answer");
  await expect(answer).toContainText("GCC 8.4 permits extension of time");
  await expect(answer).toContainText("Clause 8.4 - Extension of Time");
  await expect(answer).toContainText("p.42");
});

test("contract appraisal generates and opens a cited report", async ({ page }) => {
  await page.goto("/contracts/appraisal");

  await selectRadixOption(page, "contract-appraisal-org-select", org.name);
  await selectRadixOption(page, "contract-appraisal-project-select", project.name);
  await page.getByTestId("contract-appraisal-generate").click();

  const detail = page.getByTestId("contract-appraisal-report-detail");
  await expect(detail).toContainText("Version 1", { timeout: 15_000 });
  await expect(detail).toContainText("Complete document set");
  await expect(detail).toContainText("Time for Completion");
  await expect(detail).toContainText("GCC 8.4 governs extension of time");
});

test("contract search controls remain usable on mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/contracts/search");

  await expect(page.getByRole("heading", { name: "Search Contract Clauses" })).toBeVisible();
  await expect(page.getByTestId("contract-search-org-select")).toBeVisible();
  await expect(page.getByTestId("contract-search-query-input")).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(hasHorizontalOverflow).toBe(false);
});
