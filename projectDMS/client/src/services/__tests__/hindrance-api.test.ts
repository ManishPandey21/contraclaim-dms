import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
vi.mock("../api", () => ({ api: http }));

import {
  archiveHindrance,
  hindrancePatch,
  linkHindrance,
  listHindrances,
  listHindrancesAffecting,
  listProgrammeMilestones,
  unlinkHindrance,
  updateHindrance,
  type HindranceDTO,
} from "../hindrance-api";

const before: HindranceDTO = {
  id: "h-1",
  project_id: "proj-A1",
  event_type: "hindrance",
  title: "Access",
  start_date: "2026-02-10T00:00:00",
  end_date: "2026-02-12T00:00:00",
  responsibility: "employer",
  location: "S2",
  critical_path_impact: false,
  status: "open",
  linked_document_ids: [],
  created_at: "2026-02-10T00:00:00",
};

describe("hindrance-api", () => {
  beforeEach(() => vi.clearAllMocks());

  it("calls the canonical register routes, never the compatibility API", async () => {
    http.get.mockResolvedValue({ data: { items: [], total: 0, skip: 0, limit: 25 } });
    http.post.mockResolvedValue({ data: {} });
    http.patch.mockResolvedValue({ data: {} });

    await listHindrances({ project_id: "proj-A1", q: "", status: undefined, include_archived: true });
    await updateHindrance("h 1", { title: "x" });
    await archiveHindrance("h-1", "Duplicate");
    await linkHindrance("h-1", { target_type: "key_date", target_id: "kd-1" });
    await unlinkHindrance("h-1", "link/1", "Wrong");
    http.get.mockResolvedValue({ data: { items: [] } });
    await listHindrancesAffecting("key_date", "kd-1");

    expect(http.get).toHaveBeenCalledWith("/hindrances", { params: { project_id: "proj-A1", include_archived: true } });
    expect(http.patch).toHaveBeenCalledWith("/hindrances/h%201", { title: "x" });
    expect(http.post).toHaveBeenCalledWith("/hindrances/h-1/archive", { reason: "Duplicate" });
    expect(http.post).toHaveBeenCalledWith("/hindrances/h-1/links", { target_type: "key_date", target_id: "kd-1" });
    expect(http.post).toHaveBeenCalledWith("/hindrances/h-1/links/link%2F1/remove", { reason: "Wrong" });
    expect(http.get).toHaveBeenCalledWith("/hindrances/affecting/key_date/kd-1");
    for (const call of [...http.get.mock.calls, ...http.post.mock.calls, ...http.patch.mock.calls]) {
      expect(String(call[0])).not.toContain("delay-events");
    }
  });

  it("normalises the list response defensively", async () => {
    http.get.mockResolvedValue({ data: { items: "not-a-list" } });
    await expect(listHindrances()).resolves.toEqual({ items: [], total: 0, skip: 0, limit: 0 });
  });

  it("maps programme activities from the evidence register shape", async () => {
    http.get.mockResolvedValue({ data: [{ _id: "act-1", milestone_ref: "ACT-1", title: "Piling", milestone_type: "programme_activity", status: "planned", project_id: "p" }] });
    await expect(listProgrammeMilestones("p")).resolves.toEqual([
      { id: "act-1", milestone_ref: "ACT-1", title: "Piling", milestone_type: "programme_activity", status: "planned", planned_date: null, project_id: "p" },
    ]);
  });

  it("builds a PATCH of changed keys only, sending null to clear", () => {
    expect(
      hindrancePatch(before, {
        project_id: "proj-A1",
        event_type: "hindrance",
        title: "Access (revised)",
        start_date: "2026-02-10T00:00:00",
        end_date: null,
        responsibility: "employer",
        location: "S2",
        critical_path_impact: false,
        status: "open",
      }),
    ).toEqual({ title: "Access (revised)", end_date: null });
  });

  it("never lets scope into a PATCH", () => {
    const patch = hindrancePatch(before, {
      organization_id: "org-B",
      project_id: "proj-B1",
      event_type: "hindrance",
      title: "Access",
      start_date: "2026-02-10T00:00:00",
    });
    expect(patch).not.toHaveProperty("project_id");
    expect(patch).not.toHaveProperty("organization_id");
  });
});
