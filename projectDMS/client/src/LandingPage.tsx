import React, { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  BellRing,
  Building2,
  CheckCircle2,
  ClipboardCheck,
  FileSearch,
  FileText,
  FolderTree,
  HelpCircle,
  KeyRound,
  LockKeyhole,
  PenLine,
  Scale,
  Search,
  ShieldCheck,
  UploadCloud,
  Users,
} from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { publicApi } from "@/services/http";

// Honest capability strip — the reference mockup used fictional customer
// logos and uncertified compliance badges; those are deliberately not
// reproduced on the real product site.
const trustSignals = [
  "Built for construction & infrastructure disputes",
  "Answers cited to clause & page",
  "Permission-aware, multi-project",
  "Contractual time-bar tracking",
];

const matterTabs = ["Correspondence", "Clauses", "Evidence", "Claims & notices"];

const matterItems = [
  {
    tag: "Notice of Delay · Cl. 20.1",
    title: "RE: EOT — Foundation works, Grid B",
    note: "Contractor asserts entitlement under Sub-Clause 8.4…",
    due: "2 days left",
    urgent: true,
  },
  {
    tag: "Payment · Cl. 14.6",
    title: "IPC-11 — Engineer's certificate",
    note: "Certified sum disputed; retention release query…",
    due: "This week",
    urgent: false,
  },
  {
    tag: "Variation · Cl. 13.3",
    title: "VO-42 — Cladding specification change",
    note: "Instruction pending valuation and acceptance…",
    due: "Open",
    urgent: false,
  },
];

const features = [
  {
    icon: FileSearch,
    title: "Clause & evidence retrieval",
    description:
      "Ask in plain language and surface the exact clause, letter or exhibit — every answer cited back to its source document and page.",
    points: ["Semantic clause search", "Cited contract Q&A", "Source-linked evidence"],
  },
  {
    icon: PenLine,
    title: "Grounded reply drafting",
    description:
      "Draft contractual correspondence backed by cited clauses and precedent, through a governed input → draft → review → approval workflow.",
    points: ["Governed drafting service", "Cited, never ungrounded", "Templates & quality tracking"],
  },
  {
    icon: Scale,
    title: "Claim & deadline tracking",
    description:
      "Track every notice, time-bar and claim status across projects so nothing slips past a contractual deadline.",
    points: ["EOT, variation & payment claims", "Time-bar & SLA tracking", "Notice & evidence alignment"],
  },
];

const steps = [
  {
    icon: UploadCloud,
    title: "Ingest your record",
    text: "Upload contracts, correspondence and exhibits. ContraClaim scans, indexes and links them automatically.",
  },
  {
    icon: Search,
    title: "Ask & retrieve",
    text: "Find the governing clause and supporting evidence in seconds — each answer cited to its source document and page.",
  },
  {
    icon: ShieldCheck,
    title: "Respond & defend",
    text: "Draft grounded replies and export a defensible, audit-ready trail for any review, negotiation or tribunal.",
  },
];

const moduleCatalog = [
  {
    icon: FileSearch,
    title: "Contract intelligence",
    description:
      "Upload contracts, run semantic clause search, ask cited questions, and generate structured appraisal reports covering obligations, risks and key dates.",
  },
  {
    icon: PenLine,
    title: "Correspondence drafting service",
    description:
      "A governed drafting workflow for contractual letters — input, strategy, draft, review, approval and completion — with reusable templates and a quality dashboard.",
  },
  {
    icon: Scale,
    title: "Claims & SLA tracking",
    description:
      "Register claims (EOT, variation, payment, loss & expense) and track response deadlines and contractual time-bars so entitlements are protected.",
  },
  {
    icon: FileText,
    title: "Document management",
    description:
      "Upload, classify, tag, view, share and download project documents with rich metadata, references and structured folder browsing.",
  },
  {
    icon: Building2,
    title: "Organizations & projects",
    description:
      "Scope every document, user, party and correspondence thread to the right organization and project.",
  },
  {
    icon: Users,
    title: "Stakeholders & email groups",
    description:
      "Maintain parties, representatives, concerns and email groups used across project teams and correspondence.",
  },
  {
    icon: ShieldCheck,
    title: "Access control & SSO",
    description:
      "Govern user, role, permission, project, document and download access, with SSO / OIDC sign-in support.",
  },
  {
    icon: BarChart3,
    title: "Dashboards & reporting",
    description:
      "Track activity, correspondence status, organizational summaries, reports and analytics, plus a system health view.",
  },
  {
    icon: BellRing,
    title: "Notifications & tasks",
    description:
      "Stay on top of work with a notification center, task tracking and team activity loops.",
  },
];

const governanceDetails = [
  {
    icon: Building2,
    title: "Tenant & project isolation",
    text: "Every query, document and retrieval is scoped to its organization and project.",
  },
  {
    icon: KeyRound,
    title: "Role-based permissions",
    text: "Granular control over users, roles, projects, documents, folders and downloads, with SSO / OIDC sign-in.",
  },
  {
    icon: ClipboardCheck,
    title: "Audit-oriented activity",
    text: "Generation, edits, approvals, downloads and exports are recorded for traceability.",
  },
  {
    icon: ShieldCheck,
    title: "Safe uploads",
    text: "Uploaded files are virus-scanned before they are stored or processed.",
  },
  {
    icon: LockKeyhole,
    title: "Controlled downloads",
    text: "Files are served through permission checks and time-limited, signed access links.",
  },
  {
    icon: FileSearch,
    title: "Grounded answers",
    text: "Contract answers and appraisals cite the source document, clause and page — or state when information is not found.",
  },
];

const faqs = [
  {
    q: "What is ContraClaim DMS?",
    a: "A contract-disputes workspace that unites contract intelligence, grounded correspondence drafting, claim and deadline tracking, and governed document management for construction and infrastructure teams.",
  },
  {
    q: "How does contract Q&A stay accurate?",
    a: "Answers are grounded in your uploaded contracts and cite the source document, clause and page. When the contract does not cover something, the system says so instead of guessing.",
  },
  {
    q: "Is correspondence drafting fully automated?",
    a: "No — drafting is delivered as a governed service. Letters move through input, strategy, drafting, review and approval, so a person stays in control of every outgoing document.",
  },
  {
    q: "How are claims and deadlines handled?",
    a: "Claims (EOT, variation, payment, loss & expense) are tracked in a register alongside response deadlines and contractual time-bars, so entitlements are protected before dates compress.",
  },
  {
    q: "How is access controlled?",
    a: "Work is scoped by organization and project, with role-based permissions, SSO / OIDC sign-in, audit-oriented activity records and controlled, signed download links.",
  },
  {
    q: "Can it handle scanned documents?",
    a: "Yes — uploaded documents are processed and indexed so their text and clauses become searchable across the workspace.",
  },
];

type ContactStatus =
  | { type: "idle"; message: "" }
  | { type: "success"; message: string }
  | { type: "error"; message: string };

const LandingPage = () => {
  const [contactStatus, setContactStatus] = useState<ContactStatus>({
    type: "idle",
    message: "",
  });
  const [submittingContact, setSubmittingContact] = useState(false);
  const [activeTab, setActiveTab] = useState(0);

  const handleContactSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmittingContact(true);
    setContactStatus({ type: "idle", message: "" });

    const form = event.currentTarget;
    const data = new FormData(form);
    const payload = {
      name: String(data.get("name") || "").trim(),
      email: String(data.get("email") || "").trim(),
      organization: String(data.get("organization") || "").trim() || undefined,
      phone: String(data.get("phone") || "").trim() || undefined,
      message: String(data.get("message") || "").trim(),
    };

    try {
      await publicApi.post("/contact", payload);
      form.reset();
      setContactStatus({
        type: "success",
        message:
          "Your message has been sent. The ContraClaim team will contact you shortly.",
      });
    } catch (error: unknown) {
      const responseMessage =
        typeof error === "object" &&
        error !== null &&
        "response" in error &&
        typeof (error as { response?: { data?: { detail?: unknown } } }).response
          ?.data?.detail === "string"
          ? (error as { response: { data: { detail: string } } }).response.data
              .detail
          : undefined;
      const fallbackMessage = error instanceof Error ? error.message : undefined;
      setContactStatus({
        type: "error",
        message:
          responseMessage ||
          fallbackMessage ||
          "Unable to send your message right now. Please try again later.",
      });
    } finally {
      setSubmittingContact(false);
    }
  };

  return (
    <div className="min-h-screen overflow-x-hidden bg-paper font-franklin text-ink antialiased">
      <header className="sticky top-0 z-50 border-b border-ink/10 bg-paper/85 backdrop-blur-xl">
        <div className="container flex h-16 items-center justify-between gap-6">
          <Link
            to="/"
            className="flex items-center gap-3"
            aria-label="ContraClaim DMS home"
          >
            <img
              src="/contraclaim2.png"
              alt="ContraClaim DMS"
              className="h-9 w-auto"
            />
          </Link>
          <nav className="hidden items-center gap-8 text-sm font-semibold text-ink/70 lg:flex">
            <a href="#platform" className="transition hover:text-brand">
              Platform
            </a>
            <a href="#how" className="transition hover:text-brand">
              How it works
            </a>
            <a href="#governance" className="transition hover:text-brand">
              Security
            </a>
            <a href="#modules" className="transition hover:text-brand">
              Modules
            </a>
            <a href="#faq" className="transition hover:text-brand">
              FAQ
            </a>
          </nav>
          <div className="flex items-center gap-3">
            <Button
              asChild
              variant="ghost"
              className="hidden text-ink hover:bg-ink/5 hover:text-brand sm:inline-flex"
            >
              <Link to="/login">Sign in</Link>
            </Button>
            <Button
              asChild
              className="gap-2 rounded-full bg-brand text-white shadow-[0_8px_20px_-6px_rgba(20,102,196,.5)] hover:bg-[#1157a8]"
            >
              <a href="#contact">
                Book a demo <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
          </div>
        </div>
      </header>

      <main>
        {/* Hero */}
        <section className="relative overflow-hidden bg-ink text-white">
          {/* Relevant contract photo, dimmed by a translucent navy wash so the
              image reads through while white text stays legible. */}
          <div
            className="pointer-events-none absolute inset-0 bg-cover bg-center"
            style={{ backgroundImage: "url('/4.jpg')" }}
            aria-hidden="true"
          />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-ink/95 via-ink/85 to-ink/70" />
          <div className="pointer-events-none absolute -right-32 -top-24 h-96 w-96 rounded-full bg-brand/25 blur-3xl" />
          <div className="pointer-events-none absolute -left-20 bottom-0 h-80 w-80 rounded-full bg-brand/10 blur-3xl" />
          <div className="container relative grid gap-12 py-16 md:py-24 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
            <div>
              <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-brand-soft">
                Contract intelligence for disputes
              </span>
              <h1 className="mt-6 font-serif text-4xl font-semibold leading-[1.06] tracking-[-0.015em] text-white md:text-6xl">
                Command every contract, correspondence&nbsp;and claim.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-white/70">
                From first notice to final award, ContraClaim keeps clauses,
                correspondence and evidence connected, cited and audit-ready —
                one source of truth for the whole dispute.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <Button
                  asChild
                  size="lg"
                  className="gap-2 rounded-full bg-brand text-white shadow-[0_8px_20px_-6px_rgba(20,102,196,.5)] hover:bg-[#1157a8]"
                >
                  <a href="#contact">
                    Book a demo <ArrowRight className="h-5 w-5" />
                  </a>
                </Button>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="rounded-full border-white/25 bg-transparent text-white hover:bg-white hover:text-ink"
                >
                  <a href="#how">See how it works</a>
                </Button>
              </div>
              <p className="mt-8 text-sm text-white/45">
                Grounded retrieval · governed drafting · data residency you control
              </p>
            </div>

            {/* Product mockup: Matter view */}
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-2 shadow-[0_30px_70px_-28px_rgba(13,27,46,.6)] backdrop-blur">
              <div className="overflow-hidden rounded-xl bg-paper text-ink">
                <div className="flex items-center justify-between border-b border-ink/10 bg-white px-4 py-3">
                  <div className="flex items-center gap-2 text-xs font-medium text-ink/50">
                    <span className="h-2.5 w-2.5 rounded-full bg-coral/70" />
                    <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
                    <span className="ml-2">app.contraclaim.com/matter/A-217</span>
                  </div>
                </div>
                <div className="px-5 py-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-brand">
                        Matter A-217
                      </p>
                      <p className="font-serif text-lg font-semibold text-ink">
                        Grid B foundation delay
                      </p>
                    </div>
                    <span className="rounded-full bg-brand-soft px-3 py-1 text-xs font-bold text-brand">
                      14 time-bars · 3 due
                    </span>
                  </div>

                  <div className="mt-4 flex gap-1 overflow-x-auto rounded-lg bg-ink/5 p-1 text-xs font-semibold">
                    {matterTabs.map((tab, i) => (
                      <button
                        key={tab}
                        type="button"
                        onClick={() => setActiveTab(i)}
                        className={`whitespace-nowrap rounded-md px-3 py-1.5 transition ${
                          activeTab === i
                            ? "bg-white text-brand shadow-sm"
                            : "text-ink/55 hover:text-ink"
                        }`}
                      >
                        {tab}
                      </button>
                    ))}
                  </div>

                  <div className="mt-4 space-y-3">
                    {matterItems.map((item) => (
                      <div
                        key={item.title}
                        className="rounded-lg border border-ink/10 bg-white p-4 shadow-sm"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-[11px] font-bold uppercase tracking-wide text-brand">
                            {item.tag}
                          </span>
                          <span
                            className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${
                              item.urgent
                                ? "bg-coral/15 text-[#c0392b]"
                                : "bg-ink/5 text-ink/55"
                            }`}
                          >
                            {item.due}
                          </span>
                        </div>
                        <p className="mt-1.5 text-sm font-semibold text-ink">
                          {item.title}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-ink/55">
                          {item.note}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Trust strip */}
          <div className="border-t border-white/10 bg-ink/40">
            <div className="container flex flex-wrap items-center justify-center gap-x-8 gap-y-3 py-5 text-center text-sm font-medium text-white/60">
              {trustSignals.map((signal) => (
                <span key={signal} className="inline-flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-brand-soft" />
                  {signal}
                </span>
              ))}
            </div>
          </div>
        </section>

        {/* Platform */}
        <section id="platform" className="bg-white py-20 md:py-28">
          <div className="container">
            <div className="mx-auto max-w-3xl text-center">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
                The platform
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                A single source of truth for the whole dispute
              </h2>
              <p className="mt-5 text-lg leading-8 text-ink/60">
                Instead of scattering claims intelligence across folders,
                inboxes, spreadsheets and disconnected PDFs, ContraClaim keeps
                correspondence, clauses and evidence connected and audit-ready.
              </p>
            </div>

            <div className="mt-14 grid gap-6 lg:grid-cols-3">
              {features.map((feature) => (
                <Card
                  key={feature.title}
                  className="border-ink/10 bg-paper shadow-sm transition hover:-translate-y-1 hover:shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)]"
                >
                  <CardContent className="p-7">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-soft text-brand">
                      <feature.icon className="h-6 w-6" />
                    </div>
                    <h3 className="mt-5 font-serif text-xl font-semibold text-ink">
                      {feature.title}
                    </h3>
                    <p className="mt-3 text-sm leading-6 text-ink/60">
                      {feature.description}
                    </p>
                    <ul className="mt-5 space-y-2">
                      {feature.points.map((point) => (
                        <li
                          key={point}
                          className="flex items-center gap-2 text-sm font-semibold text-ink/75"
                        >
                          <CheckCircle2 className="h-4 w-4 shrink-0 text-brand" />
                          {point}
                        </li>
                      ))}
                    </ul>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* How it works */}
        <section id="how" className="bg-paper py-20 md:py-28">
          <div className="container">
            <div className="mx-auto max-w-3xl text-center">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
                How it works
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                Live in days, defensible from day one
              </h2>
            </div>

            <div className="mt-14 grid gap-6 md:grid-cols-3">
              {steps.map((step, index) => (
                <div
                  key={step.title}
                  className="relative rounded-2xl border border-ink/10 bg-white p-7 shadow-sm"
                >
                  <span className="font-serif text-5xl font-semibold text-brand/15">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className="mt-3 flex h-11 w-11 items-center justify-center rounded-xl bg-ink text-white">
                    <step.icon className="h-5 w-5" />
                  </div>
                  <h3 className="mt-5 font-serif text-xl font-semibold text-ink">
                    {step.title}
                  </h3>
                  <p className="mt-2.5 text-sm leading-6 text-ink/60">
                    {step.text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Modules */}
        <section id="modules" className="bg-white py-20 md:py-28">
          <div className="container">
            <div className="max-w-3xl">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
                Modules
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                Everything the workspace includes
              </h2>
              <p className="mt-5 text-lg leading-8 text-ink/60">
                A connected set of modules across contracts, correspondence,
                claims, documents, stakeholders, access control and reporting.
              </p>
            </div>

            <div className="mt-12 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              {moduleCatalog.map((feature) => (
                <Card
                  key={feature.title}
                  className="border-ink/10 bg-paper shadow-sm transition hover:-translate-y-1 hover:border-brand/30 hover:shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)]"
                >
                  <CardContent className="p-6">
                    <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-brand-soft text-brand">
                      <feature.icon className="h-6 w-6" />
                    </div>
                    <h3 className="font-serif text-lg font-semibold text-ink">
                      {feature.title}
                    </h3>
                    <p className="mt-3 text-sm leading-6 text-ink/60">
                      {feature.description}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* Governance / Security */}
        <section id="governance" className="bg-ink py-20 text-white md:py-28">
          <div className="container grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div className="lg:sticky lg:top-24">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand-soft">
                Security & governance
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-white md:text-5xl">
                Enterprise controls, built in
              </h2>
              <p className="mt-5 text-lg leading-8 text-white/65">
                Tenant isolation, role-based permissions, audit-oriented
                activity, virus-scanned uploads and signed downloads are part of
                the workspace — and every contract answer stays grounded in your
                own documents.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {governanceDetails.map((item) => (
                <div
                  key={item.title}
                  className="rounded-xl border border-white/10 bg-white/[0.04] p-5 transition hover:bg-white/[0.07]"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand text-white">
                      <item.icon className="h-5 w-5" />
                    </div>
                    <span className="text-sm font-bold text-white">
                      {item.title}
                    </span>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/60">
                    {item.text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section id="faq" className="bg-white py-20 md:py-28">
          <div className="container">
            <div className="max-w-3xl">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
                FAQ
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                Questions teams ask first
              </h2>
            </div>
            <div className="mt-12 grid gap-5 md:grid-cols-2">
              {faqs.map((item) => (
                <Card key={item.q} className="border-ink/10 bg-paper shadow-sm">
                  <CardContent className="p-6">
                    <div className="flex items-start gap-3">
                      <HelpCircle className="mt-0.5 h-5 w-5 shrink-0 text-brand" />
                      <div>
                        <h3 className="font-serif text-base font-semibold text-ink">
                          {item.q}
                        </h3>
                        <p className="mt-2 text-sm leading-6 text-ink/60">
                          {item.a}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* Contact */}
        <section id="contact" className="bg-paper py-20 md:py-28">
          <div className="container grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
                Book a demo
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                See ContraClaim on your own record
              </h2>
              <p className="mt-5 text-lg leading-8 text-ink/60">
                Book a 30-minute walkthrough with our contracts team and see
                grounded retrieval on a live matter. Send a message and it will
                reach the ContraClaim contact mailbox directly.
              </p>
              <div className="mt-6 rounded-xl border border-ink/10 bg-white p-5 shadow-sm">
                <div className="flex items-start gap-3">
                  <FileSearch className="mt-1 h-5 w-5 text-brand" />
                  <p className="text-sm leading-6 text-ink/60">
                    Use this for product enquiries, onboarding support and
                    workspace access questions. Please don't include passwords or
                    confidential claim details in the form.
                  </p>
                </div>
              </div>
            </div>

            <Card className="border-ink/10 bg-white shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)]">
              <CardContent className="p-7">
                <form className="space-y-5" onSubmit={handleContactSubmit}>
                  {contactStatus.type !== "idle" && (
                    <Alert
                      variant={
                        contactStatus.type === "error" ? "destructive" : "default"
                      }
                      role="status"
                      aria-live="polite"
                    >
                      <AlertDescription>{contactStatus.message}</AlertDescription>
                    </Alert>
                  )}

                  <div className="grid gap-5 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="contact-name">Name</Label>
                      <Input
                        id="contact-name"
                        name="name"
                        autoComplete="name"
                        minLength={2}
                        maxLength={120}
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="contact-email">Email</Label>
                      <Input
                        id="contact-email"
                        name="email"
                        type="email"
                        autoComplete="email"
                        required
                      />
                    </div>
                  </div>

                  <div className="grid gap-5 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="contact-organization">Organization</Label>
                      <Input
                        id="contact-organization"
                        name="organization"
                        autoComplete="organization"
                        maxLength={160}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="contact-phone">Phone</Label>
                      <Input
                        id="contact-phone"
                        name="phone"
                        type="tel"
                        autoComplete="tel"
                        maxLength={60}
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="contact-message">Message</Label>
                    <Textarea
                      id="contact-message"
                      name="message"
                      minLength={10}
                      maxLength={4000}
                      required
                      className="min-h-32 resize-y"
                    />
                  </div>

                  <Button
                    type="submit"
                    size="lg"
                    className="w-full gap-2 rounded-full bg-brand text-white hover:bg-[#1157a8] sm:w-auto"
                    disabled={submittingContact}
                  >
                    {submittingContact ? "Sending..." : "Send message"}
                    <ArrowRight className="h-5 w-5" />
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        </section>

        {/* Login CTA */}
        <section className="bg-ink py-16 text-white">
          <div className="container flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
            <h2 className="max-w-2xl font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] md:text-4xl">
              Access your secured ContraClaim workspace.
            </h2>
            <Button
              asChild
              size="lg"
              className="gap-2 rounded-full bg-brand text-white shadow-[0_8px_20px_-6px_rgba(20,102,196,.5)] hover:bg-[#1157a8]"
            >
              <Link to="/login">
                Sign in <ArrowRight className="h-5 w-5" />
              </Link>
            </Button>
          </div>
        </section>
      </main>

      <footer className="border-t border-ink/10 bg-paper">
        <div className="container flex flex-col gap-4 py-8 text-sm text-ink/55 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <img
              src="/page.png"
              alt=""
              className="h-8 w-8 rounded-md object-cover"
              aria-hidden="true"
            />
            <span className="font-bold text-ink">ContraClaim DMS</span>
            <span className="text-ink/40">© 2026</span>
          </div>
          <div className="flex flex-wrap gap-5 font-semibold">
            <a href="#platform" className="hover:text-brand">
              Platform
            </a>
            <a href="#how" className="hover:text-brand">
              How it works
            </a>
            <a href="#modules" className="hover:text-brand">
              Modules
            </a>
            <a href="#faq" className="hover:text-brand">
              FAQ
            </a>
            <a href="#contact" className="hover:text-brand">
              Contact
            </a>
            <Link to="/login" className="hover:text-brand">
              Sign in
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
