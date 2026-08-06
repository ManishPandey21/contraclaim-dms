import React, { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  BellRing,
  Building2,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  FileSearch,
  FileText,
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
import LandingFooter from "@/components/landing/LandingFooter";
import LandingHeader from "@/components/landing/LandingHeader";
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

const heroProofs = [
  {
    icon: FileSearch,
    label: "Evidence-first",
    detail: "Every answer traces to its source",
  },
  {
    icon: Clock3,
    label: "Deadline-aware",
    detail: "Keep contractual dates in view",
  },
  {
    icon: ShieldCheck,
    label: "Governed by design",
    detail: "Control access across every project",
  },
];

const features = [
  {
    icon: FileSearch,
    title: "Clause & evidence retrieval",
    description:
      "Ask in plain language and pull the exact clause, letter or exhibit in seconds — every answer cited back to its source document and page, so your position is always defensible.",
    points: ["Semantic clause search", "Cited contract Q&A", "Source-linked evidence"],
  },
  {
    icon: PenLine,
    title: "Grounded reply drafting",
    description:
      "Draft sharp, contractual correspondence backed by cited clauses and precedent, through a governed input → draft → review → approval workflow — never an ungrounded word goes out the door.",
    points: ["Governed drafting service", "Cited, never ungrounded", "Templates & quality tracking"],
  },
  {
    icon: Scale,
    title: "Claim & deadline tracking",
    description:
      "Stay ahead of every notice, time-bar and claim across projects — so you never surrender an entitlement to a missed date.",
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
      <LandingHeader />

      <main>
        {/* Hero */}
        <section className="relative isolate overflow-hidden bg-ink text-white">
          {/* Relevant contract photo, dimmed by a translucent navy wash so the
              image reads through while white text stays legible. */}
          <div
            className="pointer-events-none absolute inset-0 bg-cover bg-center"
            style={{ backgroundImage: "url('/contract-intelligence-hero.jpg')" }}
            aria-hidden="true"
          />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-ink via-ink/90 to-ink/55" />
          <div className="pointer-events-none absolute -right-32 -top-24 h-96 w-96 rounded-full bg-brand/20 blur-3xl" />
          <div className="pointer-events-none absolute -left-20 bottom-0 h-80 w-80 rounded-full bg-brand/10 blur-3xl" />
          <div className="container relative grid gap-14 py-20 md:py-28 lg:grid-cols-2 lg:items-center">
            <div className="min-w-0">
              <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.06] px-3.5 py-1.5 text-xs font-semibold uppercase tracking-[0.14em] text-brand-soft">
                A governed workspace for contract teams
              </span>
              <h1 className="mt-6 max-w-3xl font-serif text-4xl font-semibold leading-[1.04] tracking-[-0.02em] text-white md:text-6xl">
                Command every contract, correspondence and claim.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-white/72">
                Keep clauses, correspondence and evidence connected in one
                defensible record — from first notice through final award.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <Button
                  asChild
                  size="lg"
                  className="gap-2 rounded-full bg-brand text-white shadow-[0_8px_20px_-6px_rgba(20,102,196,.5)] hover:bg-[#1157a8]"
                >
                  <a href="#contact">
                    Request a demo <ArrowRight className="h-5 w-5" />
                  </a>
                </Button>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="rounded-full border-white/25 bg-transparent text-white hover:bg-white hover:text-ink"
                >
                  <a href="#how">Explore the workflow</a>
                </Button>
              </div>
              <div className="mt-10 grid max-w-2xl gap-4 sm:grid-cols-3">
                {heroProofs.map((proof) => (
                  <div key={proof.label} className="border-l border-white/20 pl-3">
                    <proof.icon className="h-4 w-4 text-brand-soft" />
                    <p className="mt-3 text-sm font-bold text-white">{proof.label}</p>
                    <p className="mt-1 text-xs leading-5 text-white/55">{proof.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="relative mx-auto min-w-0 w-full max-w-[660px]">
              <div className="pointer-events-none absolute -inset-8 rounded-[2rem] bg-brand/15 blur-3xl" />
              <div className="relative overflow-hidden rounded-2xl border border-white/15 bg-[#0a1424]/70 p-2 shadow-[0_34px_80px_-34px_rgba(0,0,0,.9)] backdrop-blur-sm">
                <div className="flex items-center justify-between px-3 py-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-white/55">
                  <span>ContraClaim workspace</span>
                  <span className="inline-flex items-center gap-1.5 text-brand-soft">
                    <CheckCircle2 className="h-3.5 w-3.5" /> Source-linked
                  </span>
                </div>
                <img
                  src="/dashboard-hero.png"
                  alt="ContraClaim Document Management System dashboard — document totals, status breakdown and recent activity"
                  width={621}
                  height={402}
                  loading="eager"
                  className="h-auto w-full rounded-xl border border-white/10 [filter:drop-shadow(0_24px_36px_rgba(0,0,0,.4))] transition-transform duration-500 ease-out hover:scale-[1.01]"
                />
              </div>
              <div className="absolute -bottom-5 -left-4 hidden rounded-xl border border-white/15 bg-ink/90 px-4 py-3 shadow-xl backdrop-blur-md sm:block">
                <p className="text-xs font-semibold text-white">Trace every position</p>
                <p className="mt-1 text-xs text-white/55">Clause, page and evidence in context</p>
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
                Built for certainty
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                Every position starts with the record
              </h2>
              <p className="mt-5 text-lg leading-8 text-ink/60">
                Replace scattered folders, inboxes and disconnected PDFs with a
                connected, audit-ready record — so every position is backed by
                the document.
              </p>
            </div>

            <div className="mt-14 grid gap-6 lg:grid-cols-3">
              {features.map((feature) => (
                <Card
                  key={feature.title}
                  className="rounded-2xl border-ink/10 bg-paper shadow-sm transition hover:-translate-y-1 hover:border-brand/25 hover:shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)]"
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
        <section
          id="how"
          className="relative isolate overflow-hidden border-y border-brand/10 bg-[#f3f7fc] py-20 md:py-28"
        >
          {/* Subtle project artwork keeps the workflow grounded in the sector
              without competing with the steps themselves. */}
          <div className="pointer-events-none absolute inset-0 -z-20" aria-hidden="true">
            <div
              className="absolute inset-0 bg-cover bg-center bg-no-repeat opacity-40"
              style={{
                backgroundImage: "url('/Infrastructure.png')",
              }}
            />
            <div className="absolute inset-0 bg-gradient-to-b from-[#f3f7fc]/95 via-[#f3f7fc]/75 to-[#f3f7fc]" />
          </div>

          <div className="container relative z-10">
            <div className="mx-auto max-w-3xl text-center">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
                How it works
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                Move from record to response with confidence
              </h2>
              <p className="mx-auto mt-5 max-w-2xl text-lg leading-8 text-ink/60">
                A focused workflow keeps the right evidence, decision-makers and
                contractual dates together at every stage.
              </p>
            </div>

            <div className="mt-14 grid gap-6 md:grid-cols-3">
              {steps.map((step, index) => (
                <div
                  key={step.title}
                  className="group relative overflow-hidden rounded-2xl border border-brand/15 bg-white/90 p-7 shadow-[0_22px_55px_-38px_rgba(20,102,196,0.5)] backdrop-blur-[2px] transition duration-300 hover:-translate-y-1 hover:border-brand/30 hover:shadow-[0_30px_70px_-38px_rgba(20,102,196,0.62)]"
                >
                  <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand/35 to-transparent" />
                  <span className="font-serif text-5xl font-semibold text-brand/20 transition-colors duration-300 group-hover:text-brand/30">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className="mt-3 flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-white shadow-[0_10px_24px_-10px_rgba(20,102,196,0.9)] transition duration-300 group-hover:-translate-y-0.5 group-hover:bg-[#1157a8]">
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
                One connected platform
              </p>
              <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
                The full record, without the fragmentation
              </h2>
              <p className="mt-5 text-lg leading-8 text-ink/60">
                One connected set of modules across contracts, correspondence,
                claims, documents, stakeholders, access control and reporting.
                The context stays intact wherever work happens.
              </p>
            </div>

            <div className="mt-12 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              {moduleCatalog.map((feature) => (
                <Card
                  key={feature.title}
                  className="rounded-2xl border-ink/10 bg-paper shadow-sm transition hover:-translate-y-1 hover:border-brand/30 hover:shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)]"
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
        <section
          id="governance"
          aria-labelledby="governance-heading"
          className="relative overflow-hidden bg-ink py-20 text-white md:py-28"
        >
          {/* Relevant security/contract photo behind a navy wash so the six
              cards stay readable while the image reads through. */}
          <div
            className="pointer-events-none absolute inset-0 bg-cover bg-center"
            style={{ backgroundImage: "url('/contract-security-governance.jpg')" }}
            aria-hidden="true"
          />
          <div className="pointer-events-none absolute inset-0 bg-ink/75" />
          <div className="container relative z-10 grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div className="lg:sticky lg:top-24">
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand-soft">
                Security & governance
              </p>
              <h2
                id="governance-heading"
                className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-white md:text-5xl"
              >
                Governance you can stand behind
              </h2>
              <p className="mt-5 text-lg leading-8 text-white/65">
                Tenant isolation, role-based permissions, audit-oriented
                activity, virus-scanned uploads and signed downloads are part of
                the workspace. Every contract answer remains grounded in your
                own record.
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
                Straight answers, up front
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
        <section
          id="contact"
          className="relative isolate overflow-hidden bg-[#eff6ff] py-20 md:py-28"
        >
          <div
            className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(circle_at_84%_16%,rgba(20,102,196,0.14),transparent_28%),radial-gradient(circle_at_8%_84%,rgba(13,27,46,0.08),transparent_28%)]"
            aria-hidden="true"
          />

          <div className="container relative grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
    <div>
      <p className="text-sm font-bold uppercase tracking-[0.14em] text-brand">
        Request a demo
      </p>

      <h2 className="mt-4 font-serif text-3xl font-semibold leading-tight tracking-[-0.015em] text-ink md:text-5xl">
        See the record behind every decision
      </h2>

      <p className="mt-5 text-lg leading-8 text-ink/70">
        Book a 30-minute walkthrough with our contracts team. We will show how
        ContraClaim brings retrieval, drafting and deadlines together in one
        governed workspace.
      </p>

      <div className="mt-7 rounded-2xl border border-ink/10 bg-white/70 p-5 shadow-sm backdrop-blur-sm">
        <div className="flex items-start gap-3">
          <FileSearch className="mt-1 h-5 w-5 text-brand" />
          <p className="text-sm leading-6 text-ink/65">
            Use this form for product enquiries, onboarding support and
            workspace access questions. Please do not include passwords or
            confidential claim details.
          </p>
        </div>
      </div>
    </div>
	


            <Card className="rounded-2xl border-ink/10 bg-white shadow-[0_30px_70px_-40px_rgba(13,27,46,.4)]">
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

      <LandingFooter />
    </div>
  );
};

export default LandingPage;
