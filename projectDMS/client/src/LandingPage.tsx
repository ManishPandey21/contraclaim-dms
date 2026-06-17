import React, { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  BellRing,
  Bot,
  BrainCircuit,
  Building2,
  CheckCircle2,
  ClipboardCheck,
  FileSearch,
  FileText,
  FolderTree,
  HelpCircle,
  KeyRound,
  LockKeyhole,
  Network,
  PenLine,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Users,
  Zap,
} from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { publicApi } from "@/services/http";

const proofMetrics = [
  { value: "360°", label: "contract, claim & correspondence visibility" },
  { value: "Cited", label: "answers linked to clause & page" },
  { value: "RBAC", label: "permission-aware, multi-project" },
  { value: "SLA", label: "time-bar & deadline tracking" },
];

const platformSignals = [
  { icon: FolderTree, label: "Project folder structure" },
  { icon: Search, label: "Semantic search and filters" },
  { icon: UploadCloud, label: "Document and contract upload" },
  { icon: ShieldCheck, label: "Governed access control" },
];

const workflows = [
  {
    icon: BrainCircuit,
    title: "Contract intelligence & appraisal",
    description:
      "Search clauses, ask questions with answers cited to document, clause and page, and generate a structured contract appraisal report.",
    points: [
      "Semantic clause search",
      "Cited contract Q&A",
      "Structured appraisal report",
    ],
    accent: "from-cyan-400 to-blue-500",
  },
  {
    icon: PenLine,
    title: "Correspondence drafting as a service",
    description:
      "Produce contractual letters through a governed, end-to-end drafting service with built-in review, approval and quality tracking.",
    points: [
      "Input → strategy → draft",
      "Review → approval → completed",
      "Templates & quality dashboard",
    ],
    accent: "from-indigo-400 to-blue-500",
  },
  {
    icon: Scale,
    title: "Claims & SLA readiness",
    description:
      "Register EOT, variation, payment and loss/expense claims, and keep notices, evidence and time-bars aligned before deadlines compress.",
    points: [
      "Claims register",
      "Time-bar & SLA tracking",
      "Notice & evidence alignment",
    ],
    accent: "from-sky-400 to-cyan-500",
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
    icon: Search,
    title: "Search & retrieval",
    description:
      "Semantic and filtered search across documents and contracts, with metadata-rich results and traceable source references.",
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

const lifecycleSteps = [
  {
    title: "Set up",
    text: "Create organizations, projects, users, roles, parties and email groups so work is correctly scoped.",
    icon: Building2,
    accent: "from-blue-500 to-cyan-400",
    glow: "group-hover:shadow-cyan-200/70",
  },
  {
    title: "Ingest",
    text: "Upload contracts, letters, references and supporting records; files are scanned and indexed with metadata.",
    icon: UploadCloud,
    accent: "from-cyan-500 to-teal-400",
    glow: "group-hover:shadow-teal-200/70",
  },
  {
    title: "Understand",
    text: "Search clauses, ask cited contract questions, and generate appraisal reports with obligations, risks and key dates.",
    icon: BrainCircuit,
    accent: "from-sky-500 to-blue-400",
    glow: "group-hover:shadow-sky-200/70",
  },
  {
    title: "Draft",
    text: "Produce contractual correspondence as a service through a governed review-and-approval workflow.",
    icon: PenLine,
    accent: "from-indigo-500 to-blue-400",
    glow: "group-hover:shadow-indigo-200/70",
  },
  {
    title: "Protect",
    text: "Register claims and track notices, deadlines and time-bars so entitlements are never lost to a missed date.",
    icon: ShieldCheck,
    accent: "from-cyan-500 to-sky-400",
    glow: "group-hover:shadow-cyan-200/70",
  },
  {
    title: "Report",
    text: "Monitor status, analytics and system health from dedicated dashboards.",
    icon: BarChart3,
    accent: "from-blue-500 to-indigo-400",
    glow: "group-hover:shadow-blue-200/70",
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
    text: "Uploaded files can be virus-scanned before they are stored or processed.",
  },
  {
    icon: LockKeyhole,
    title: "Controlled downloads",
    text: "Files are served through permission checks and time-limited, signed access links.",
  },
  {
    icon: BrainCircuit,
    title: "Grounded answers",
    text: "Contract answers and appraisals cite the source document, clause and page — or state when information is not found.",
  },
];

const faqs = [
  {
    q: "What is ContraClaim DMS?",
    a: "A document management and contract-claims workspace that unites contract intelligence, correspondence drafting, claims and deadline tracking, and governed document management for infrastructure and construction teams.",
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
    <div className="min-h-screen overflow-x-hidden bg-[#f8fbff] text-slate-950">
      <header className="sticky top-0 z-50 border-b border-white/20 bg-slate-950/85 text-white backdrop-blur-xl">
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
          <nav className="hidden items-center gap-6 text-sm font-semibold text-white/75 lg:flex">
            <a href="#platform" className="transition hover:text-cyan-200">
              Platform
            </a>
            <a href="#workflows" className="transition hover:text-cyan-200">
              Workflows
            </a>
            <a href="#modules" className="transition hover:text-cyan-200">
              Modules
            </a>
            <a href="#governance" className="transition hover:text-cyan-200">
              Governance
            </a>
            <a href="#faq" className="transition hover:text-cyan-200">
              FAQ
            </a>
            <a href="#contact" className="transition hover:text-cyan-200">
              Contact
            </a>
          </nav>
          <div className="flex items-center gap-3">
            <Button
              asChild
              variant="ghost"
              className="hidden text-white hover:bg-white/10 hover:text-white sm:inline-flex"
            >
              <Link to="/login">Login</Link>
            </Button>
            <Button
              asChild
              className="hidden gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200 sm:inline-flex"
            >
              <a href="#contact">
                Book a walkthrough <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
          </div>
        </div>
      </header>

      <main>
        <section
          className="relative min-h-[72vh] overflow-hidden bg-cover bg-center text-white"
          style={{
            backgroundImage:
              "linear-gradient(105deg, rgba(2, 6, 23, 0.98), rgba(30, 41, 59, 0.84), rgba(12, 74, 110, 0.48)), url('/New%20folder/automated-contracts.jpg')",
          }}
        >
          <div className="absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-[#f8fbff] to-transparent" />
          <div className="container relative py-16 md:py-24">
            <div
              className="max-w-5xl"
              style={{ maxWidth: "min(64rem, calc(100vw - 4rem))" }}
            >
              <div className="flex w-full max-w-[calc(100vw-4rem)] items-start gap-2 rounded-md border border-cyan-200/30 bg-cyan-200/10 px-3 py-2 text-sm font-semibold text-cyan-100 sm:inline-flex sm:w-auto sm:max-w-full">
                <Sparkles className="mt-0.5 h-4 w-4 shrink-0" />
                <span className="min-w-0 whitespace-normal break-words sm:hidden">
                  Contract & claim workspace
                </span>
                <span className="hidden sm:inline">
                  Contract intelligence, drafting service & claim command center
                </span>
              </div>
              <h1 className="mt-6 max-w-5xl text-4xl font-black leading-[1.04] text-white md:text-6xl">
                Turn every project record into a searchable claim advantage.
              </h1>
              <p className="mt-6 max-w-3xl text-lg leading-8 text-slate-100 md:text-xl">
                ContraClaim DMS unites contract intelligence, correspondence
                drafting delivered as a service, claims and deadline tracking,
                and governed document management — in one workspace for
                infrastructure and construction teams.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <Button
                  asChild
                  size="lg"
                  className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200"
                >
                  <Link to="/login">
                    Launch workspace <ArrowRight className="h-5 w-5" />
                  </Link>
                </Button>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="border-white/50 bg-white/10 text-white hover:bg-white hover:text-slate-950"
                >
                  <a href="#workflows">Explore workflows</a>
                </Button>
              </div>
            </div>

            <div
              className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
              style={{ maxWidth: "calc(100vw - 4rem)" }}
            >
              {proofMetrics.map((metric) => (
                <div
                  key={metric.label}
                  className="rounded-lg border border-white/15 bg-white/10 p-4 backdrop-blur-md"
                >
                  <p className="text-3xl font-black text-cyan-200">
                    {metric.value}
                  </p>
                  <p className="mt-1 text-sm font-medium text-slate-100">
                    {metric.label}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="relative -mt-8 pb-12">
          <div className="container">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {platformSignals.map((item) => (
                <div
                  key={item.label}
                  className="rounded-lg border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/70"
                >
                  <div className="flex items-center gap-4">
                    <div className="flex h-11 w-11 items-center justify-center rounded-md bg-gradient-to-br from-sky-400 via-blue-500 to-indigo-500 text-white">
                      <item.icon className="h-5 w-5" />
                    </div>
                    <p className="text-sm font-bold text-slate-800">
                      {item.label}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section
          id="platform"
          className="relative overflow-hidden bg-gradient-to-br from-sky-50 via-blue-50/60 to-cyan-50 py-16 md:py-24"
        >
          <div className="pointer-events-none absolute -left-24 top-0 h-72 w-72 rounded-full bg-sky-300/30 blur-3xl" />
          <div className="pointer-events-none absolute -right-20 bottom-0 h-80 w-80 rounded-full bg-cyan-300/30 blur-3xl" />
          <div className="container relative grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
            <div>
              <p className="text-sm font-bold text-blue-600">Platform</p>
              <h2 className="mt-3 text-3xl font-black leading-tight text-slate-950 md:text-5xl">
                A live operating layer for contracts, evidence, and project
                correspondence.
              </h2>
              <p className="mt-5 text-lg leading-8 text-slate-600">
                Instead of spreading claims intelligence across folders, inboxes,
                spreadsheets, and disconnected PDFs, ContraClaim gives teams a
                unified contract memory with workflow control.
              </p>
            </div>

            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl shadow-blue-100/70">
              <div className="border-b border-slate-200 bg-slate-950 px-5 py-4 text-white">
                <div className="flex items-center justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <Bot className="h-5 w-5 text-cyan-300" />
                    <span className="text-sm font-bold">
                      ContraClaim intelligence desk
                    </span>
                  </div>
                  <span className="rounded-md bg-emerald-300 px-2 py-1 text-xs font-bold text-slate-950">
                    Live
                  </span>
                </div>
              </div>
              <div className="grid gap-0 md:grid-cols-[0.85fr_1.15fr]">
                <div className="border-b border-slate-200 bg-gradient-to-br from-sky-50 via-white to-blue-50 p-5 md:border-b-0 md:border-r">
                  <p className="text-sm font-bold text-slate-700">
                    Ask across project memory
                  </p>
                  <div className="mt-4 rounded-lg bg-slate-950 p-4 text-white">
                    <p className="text-sm leading-6 text-slate-200">
                      “Show open notices tied to delayed drawings and list
                      evidence gaps before the next review.”
                    </p>
                  </div>
                  <div className="mt-4 grid gap-3">
                    {["Contracts", "Letters", "References", "Parties"].map(
                      (source) => (
                        <div
                          key={source}
                          className="flex items-center justify-between rounded-md bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm"
                        >
                          {source}
                          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        </div>
                      ),
                    )}
                  </div>
                </div>
                <div className="p-5">
                  <div className="grid gap-4">
                    {[
                      {
                        label: "Related contractual clauses",
                        color: "bg-cyan-500",
                        width: "w-10/12",
                      },
                      {
                        label: "Linked correspondence trail",
                        color: "bg-indigo-500",
                        width: "w-8/12",
                      },
                      {
                        label: "Review-ready evidence pack",
                        color: "bg-sky-500",
                        width: "w-9/12",
                      },
                      {
                        label: "Role-gated download history",
                        color: "bg-blue-400",
                        width: "w-7/12",
                      },
                    ].map((row) => (
                      <div key={row.label}>
                        <div className="mb-2 flex items-center justify-between text-sm font-semibold text-slate-700">
                          <span>{row.label}</span>
                          <Zap className="h-4 w-4 text-blue-500" />
                        </div>
                        <div className="h-3 rounded-full bg-slate-100">
                          <div
                            className={`h-3 rounded-full ${row.color} ${row.width}`}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section
          id="workflows"
          className="relative overflow-hidden bg-gradient-to-br from-blue-950 via-blue-900 to-cyan-900 py-16 text-white md:py-24"
        >
          <div className="pointer-events-none absolute right-0 top-0 h-96 w-96 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="pointer-events-none absolute -left-20 bottom-0 h-80 w-80 rounded-full bg-blue-400/10 blur-3xl" />
          <div className="container relative">
            <div className="max-w-3xl">
              <p className="text-sm font-bold text-cyan-200">Workflows</p>
              <h2 className="mt-3 text-3xl font-black leading-tight md:text-5xl">
                Built around how contract and claims teams actually work.
              </h2>
              <p className="mt-5 text-lg leading-8 text-slate-300">
                Each workflow connects document memory, contract context, and
                operational governance so teams can move faster without losing
                control.
              </p>
            </div>

            <div className="mt-10 grid gap-5 lg:grid-cols-3">
              {workflows.map((workflow) => (
                <Card
                  key={workflow.title}
                  className="border-white/10 bg-white/[0.04] text-white shadow-none"
                >
                  <CardContent className="p-6">
                    <div
                      className={`flex h-12 w-12 items-center justify-center rounded-md bg-gradient-to-br ${workflow.accent} text-white`}
                    >
                      <workflow.icon className="h-6 w-6" />
                    </div>
                    <h3 className="mt-5 text-xl font-black text-white">
                      {workflow.title}
                    </h3>
                    <p className="mt-3 text-sm leading-6 text-slate-300">
                      {workflow.description}
                    </p>
                    <ul className="mt-5 space-y-2">
                      {workflow.points.map((point) => (
                        <li
                          key={point}
                          className="flex items-center gap-2 text-sm font-semibold text-slate-200"
                        >
                          <CheckCircle2 className="h-4 w-4 shrink-0 text-cyan-300" />
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

        <section className="relative overflow-hidden bg-gradient-to-br from-sky-50 via-white to-blue-50 py-16 md:py-24">
          <div className="pointer-events-none absolute -left-24 top-10 h-72 w-72 rounded-full bg-sky-300/40 blur-3xl" />
          <div className="pointer-events-none absolute -right-24 bottom-0 h-80 w-80 rounded-full bg-blue-300/40 blur-3xl" />
          <div className="container relative grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div className="lg:sticky lg:top-24">
              <span className="inline-flex items-center gap-2 rounded-full border border-blue-200 bg-white/70 px-3 py-1 text-sm font-bold text-blue-600 shadow-sm backdrop-blur">
                <Sparkles className="h-4 w-4" />
                How it works
              </span>
              <h2 className="mt-4 text-3xl font-black leading-tight md:text-5xl">
                <span className="bg-gradient-to-r from-sky-600 via-blue-600 to-indigo-600 bg-clip-text text-transparent">
                  From setup to protected entitlement.
                </span>
              </h2>
              <p className="mt-5 text-lg leading-8 text-slate-600">
                One connected lifecycle moves a project from structure and
                ingestion, through understanding and drafting, to claim
                protection and reporting.
              </p>
            </div>

            <ol className="relative space-y-4 before:absolute before:left-[47px] before:top-6 before:bottom-6 before:w-0.5 before:bg-gradient-to-b before:from-sky-300 before:via-blue-300 before:to-indigo-300">
              {lifecycleSteps.map((step, index) => (
                <li
                  key={step.title}
                  className={`group relative flex gap-4 rounded-2xl border border-white/80 bg-white/80 p-5 shadow-md shadow-slate-200/60 backdrop-blur transition-all duration-300 hover:-translate-y-1 hover:shadow-xl ${step.glow} sm:gap-5`}
                >
                  <div className="relative shrink-0">
                    <div
                      className={`flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-br ${step.accent} text-white shadow-lg transition-transform duration-300 group-hover:scale-110`}
                    >
                      <step.icon className="h-6 w-6" />
                    </div>
                    <span
                      className={`absolute -right-1.5 -top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-white text-xs font-black text-slate-900 shadow ring-2 ring-white`}
                    >
                      {index + 1}
                    </span>
                  </div>
                  <div className="pt-1">
                    <h3 className="text-base font-black text-slate-900 md:text-lg">
                      {step.title}
                    </h3>
                    <p className="mt-1.5 text-sm font-medium leading-6 text-slate-600">
                      {step.text}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="modules" className="bg-white py-16 md:py-24">
          <div className="container">
            <div className="max-w-3xl">
              <p className="text-sm font-bold text-sky-600">Modules</p>
              <h2 className="mt-3 text-3xl font-black leading-tight text-slate-950 md:text-5xl">
                Everything the workspace includes.
              </h2>
              <p className="mt-5 text-lg leading-8 text-slate-600">
                A connected set of modules across contracts, correspondence,
                claims, documents, stakeholders, access control and reporting.
              </p>
            </div>

            <div className="mt-10 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              {moduleCatalog.map((feature) => (
                <Card
                  key={feature.title}
                  className="border-slate-200 bg-white shadow-sm transition hover:-translate-y-1 hover:shadow-xl hover:shadow-blue-100"
                >
                  <CardContent className="p-6">
                    <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-md bg-gradient-to-br from-blue-500 to-cyan-300 text-white">
                      <feature.icon className="h-6 w-6" />
                    </div>
                    <h3 className="text-lg font-black text-slate-950">
                      {feature.title}
                    </h3>
                    <p className="mt-3 text-sm leading-6 text-slate-600">
                      {feature.description}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        <section
          id="governance"
          className="relative overflow-hidden bg-gradient-to-br from-blue-50 via-sky-50 to-cyan-50 py-16 md:py-24"
        >
          <div className="pointer-events-none absolute -right-24 top-10 h-72 w-72 rounded-full bg-blue-300/30 blur-3xl" />
          <div className="pointer-events-none absolute -left-20 bottom-0 h-72 w-72 rounded-full bg-cyan-300/30 blur-3xl" />
          <div className="container relative grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
            <div>
              <p className="text-sm font-bold text-blue-600">Governance</p>
              <h2 className="mt-3 text-3xl font-black leading-tight text-slate-950 md:text-5xl">
                Enterprise controls without a dull enterprise experience.
              </h2>
              <p className="mt-5 text-lg leading-8 text-slate-600">
                Tenant isolation, role permissions, audit-oriented activity,
                safe uploads, and controlled downloads are built into the
                workspace — and contract answers stay grounded in your
                documents.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {governanceDetails.map((item) => (
                <div
                  key={item.title}
                  className="rounded-lg border border-sky-100 bg-white/80 p-5 shadow-sm shadow-blue-100/60 backdrop-blur transition hover:-translate-y-1 hover:shadow-lg hover:shadow-blue-200/60"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-md bg-gradient-to-br from-sky-500 to-blue-600 text-white">
                      <item.icon className="h-5 w-5" />
                    </div>
                    <span className="text-sm font-black text-slate-900">
                      {item.title}
                    </span>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-slate-600">
                    {item.text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="faq" className="bg-white py-16 md:py-24">
          <div className="container">
            <div className="max-w-3xl">
              <p className="text-sm font-bold text-blue-600">FAQ</p>
              <h2 className="mt-3 text-3xl font-black leading-tight text-slate-950 md:text-5xl">
                Questions teams ask first.
              </h2>
            </div>
            <div className="mt-10 grid gap-5 md:grid-cols-2">
              {faqs.map((item) => (
                <Card
                  key={item.q}
                  className="border-slate-200 bg-white shadow-sm"
                >
                  <CardContent className="p-6">
                    <div className="flex items-start gap-3">
                      <HelpCircle className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" />
                      <div>
                        <h3 className="text-base font-black text-slate-950">
                          {item.q}
                        </h3>
                        <p className="mt-2 text-sm leading-6 text-slate-600">
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

        <section
          id="contact"
          className="relative overflow-hidden bg-gradient-to-br from-sky-50 via-white to-blue-50 py-16 md:py-24"
        >
          <div className="pointer-events-none absolute -left-24 top-10 h-72 w-72 rounded-full bg-sky-300/30 blur-3xl" />
          <div className="pointer-events-none absolute -right-24 bottom-0 h-80 w-80 rounded-full bg-blue-300/30 blur-3xl" />
          <div className="container relative z-10 grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div>
              <p className="text-sm font-bold text-blue-600">Contact</p>
              <h2 className="mt-3 text-3xl font-black leading-tight text-slate-950 md:text-5xl">
                Build your contract command center.
              </h2>
              <p className="mt-5 text-lg leading-8 text-slate-600">
                Send a message from this page and it will be delivered to the
                configured ContraClaim contact mailbox. The recipient is managed
                safely on the backend through environment configuration.
              </p>
              <div className="mt-6 rounded-lg border border-blue-100 bg-white p-5 shadow-sm">
                <div className="flex items-start gap-3">
                  <FileSearch className="mt-1 h-5 w-5 text-blue-600" />
                  <p className="text-sm leading-6 text-slate-600">
                    Use this for product enquiries, onboarding support, and
                    workspace access questions. Do not include passwords or
                    confidential claim details in the contact form.
                  </p>
                </div>
              </div>
            </div>

            <Card className="border-slate-200 bg-white/95 shadow-xl shadow-blue-100/70">
              <CardContent className="p-6">
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
                    className="w-full gap-2 bg-slate-950 text-white hover:bg-blue-700 sm:w-auto"
                    disabled={submittingContact}
                  >
                    {submittingContact ? "Sending..." : "Send Message"}
                    <ArrowRight className="h-5 w-5" />
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        </section>

        <section className="relative overflow-hidden bg-gradient-to-r from-blue-950 via-blue-900 to-cyan-900 py-14 text-white">
          <div className="pointer-events-none absolute right-10 top-0 h-64 w-64 rounded-full bg-cyan-400/15 blur-3xl" />
          <div className="container relative flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
            <div className="max-w-3xl">
              <div className="mb-4 inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/10 px-3 py-2 text-sm font-semibold text-cyan-100">
                <Network className="h-4 w-4" />
                Connected DMS intelligence
              </div>
              <h2 className="text-3xl font-black leading-tight md:text-4xl">
                Access your secured ContraClaim DMS workspace.
              </h2>
            </div>
            <Button
              asChild
              size="lg"
              className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200"
            >
              <Link to="/login">
                Login <ArrowRight className="h-5 w-5" />
              </Link>
            </Button>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="container flex flex-col gap-4 py-8 text-sm text-slate-500 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <img
              src="/page.png"
              alt=""
              className="h-8 w-8 rounded-md object-cover"
              aria-hidden="true"
            />
            <span className="font-bold text-slate-800">ContraClaim DMS</span>
          </div>
          <div className="flex flex-wrap gap-5 font-semibold">
            <a href="#platform" className="hover:text-blue-600">
              Platform
            </a>
            <a href="#workflows" className="hover:text-blue-600">
              Workflows
            </a>
            <a href="#modules" className="hover:text-blue-600">
              Modules
            </a>
            <a href="#faq" className="hover:text-blue-600">
              FAQ
            </a>
            <a href="#contact" className="hover:text-blue-600">
              Contact
            </a>
            <Link to="/login" className="hover:text-blue-600">
              Login
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
