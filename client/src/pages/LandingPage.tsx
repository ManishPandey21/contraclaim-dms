import React, { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  Bell,
  Building2,
  CheckCircle2,
  FileSearch,
  FileText,
  FolderTree,
  LockKeyhole,
  MailCheck,
  MessageSquareText,
  ShieldCheck,
  UploadCloud,
  Users,
  Scale,
  Clock3,
  FileSignature,
  CalendarDays,
  ShieldAlert
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { publicApi } from "@/services/http";

const features = [
  {
    icon: MailCheck,
    title: "Correspondence & Claim Control",
    description:
      "Track and interconnect letters, contractual notifications, and variation orders. Prevent missed reply timelines or time-barred Clause 20.1 exposure.",
  },
  {
    icon: FileSignature,
    title: "Expert-Assisted Drafting Desk",
    description:
      "Access specialized contract engineering and claim advisory. Seamlessly request technically sound letters and notices drawn directly from your live project history.",
  },
  {
    icon: MessageSquareText,
    title: "Contract Intelligence Q&A",
    description:
      "Upload project contracts, execute cross-package clause searches, and run context-aware Q&A queries against complex concession agreements.",
  },
  {
    icon: FolderTree,
    title: "Project-Wide Chronology Logs",
    description:
      "Maintain a tamper-proof, interconnected history of variations, delay events, and engineer instructions across individual contract packages.",
  },
  {
    icon: ShieldCheck,
    title: "Enterprise RBAC Protection",
    description:
      "Safeguard critical evidence. Enforce rigorous, role-based access control, project-level data scoping, and unalterable document download audit trails.",
  },
  {
    icon: BarChart3,
    title: "Risk & Claims Dashboards",
    description:
      "Gain full executive visibility over pending responses, active dispute balances, EOT milestones, and organizational project metrics.",
  },
  {
    icon: Users,
    title: "Stakeholder Matrix Mapping",
    description:
      "Manage critical project parties, independent engineers, PMC groups, and specialized email distribution groups inside a secure ecosystem.",
  },
  {
    icon: Bell,
    title: "Active Timeline Notifications",
    description:
      "Keep commercial and project management teams aligned with real-time tracking of input requests, draft review stages, and urgent submission windows.",
  },
];

const commercialLines = [
  {
    title: "ContraClaim DMS Platform",
    badge: "SaaS Line",
    description: "Designed for infrastructure teams managing internal contract resources who require a purpose-built record engine.",
    features: [
      "Project-centric monthly/annual flat subscriptions",
      "Advanced correspondence linking & relational metadata",
      "High-volume OCR & advanced semantic search filters",
      "Multi-project dashboard & custom analytics workflows"
    ],
    cta: "Request Platform Quote",
    href: "#contact"
  },
  {
    title: "Expert Drafting + Platform Bundle",
    badge: "Premium Line",
    description: "The complete outsourced contract correspondence solution, featuring specialized engineering advisory backed by our platform.",
    features: [
      "Dedicated contract engineering advisory retainers",
      "Technically sound contractual replies, notices, & EOT logs",
      "Complementary ContraClaim DMS access included during active engagement",
      "Seamless transition to read-only archive paths post-project"
    ],
    cta: "Speak with a Contract Expert",
    href: "#contact",
    premium: true
  }
];

const benefits = [
  "Protects project bottom-lines against liquidated damages by keeping contractual timelines strictly enforced.",
  "Eradicates weak, contradictory, or legally compromised outbound letters through record-backed engineering reviews.",
  "Replaces fragmented data silos across scattered personal emails, WhatsApp text records, and broken Excel trackers.",
  "Preserves audit-grade chronological evidence for Extension of Time (EOT) applications and arbitration panels.",
];

const steps = [
  {
    title: "Initialize the Structural Workspace",
    description:
      "Map your organizations, contract packages, stakeholder groups, and custom role-based permissions before data onboarding.",
  },
  {
    title: "Ingest & Index Project Records",
    description:
      "Onboard current or legacy correspondence streams. Enrich documents with relational metadata, custom tags, and folder structures.",
  },
  {
    title: "Control, Draft, & Defend Claims",
    description:
      "Leverage workflow metrics, context-aware AI search, and expert drafting desks to issue responses and secure variations.",
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
        message: "Your inquiry has been successfully transmitted. A ContraClaim commercial specialist will connect with you shortly.",
      });
    } catch (error: unknown) {
      const responseMessage =
        typeof error === "object" &&
        error !== null &&
        "response" in error &&
        typeof (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail === "string"
          ? (error as { response: { data: { detail: string } } }).response.data.detail
          : undefined;
      const fallbackMessage = error instanceof Error ? error.message : undefined;
      setContactStatus({
        type: "error",
        message:
          responseMessage ||
          fallbackMessage ||
          "Unable to submit your request at this moment. Please reach out via phone or try again later.",
      });
    } finally {
      setSubmittingContact(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-slate-800">
      <header className="sticky top-0 z-50 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="container flex h-16 items-center justify-between gap-6">
          <Link to="/" className="flex items-center gap-3" aria-label="ContraClaim Home">
            <img src="/contraclaim2.png" alt="ContraClaim Platform" className="h-9 w-auto" />
          </Link>
          <nav className="hidden items-center gap-6 text-sm font-semibold text-slate-600 md:flex">
            <a href="#features" className="hover:text-blue-600 transition-colors">
              Platform Capabilities
            </a>
            <a href="#solutions" className="hover:text-blue-600 transition-colors">
              Commercial Lines
            </a>
            <a href="#benefits" className="hover:text-blue-600 transition-colors">
              Risk Mitigation
            </a>
            <a href="#pilot" className="hover:text-blue-600 transition-colors">
              90-Day Pilot
            </a>
          </nav>
          <div className="flex items-center gap-4">
            <Button asChild variant="ghost" className="hidden sm:inline-flex text-slate-600 font-semibold">
              <Link to="/login">Sign In</Link>
            </Button>
            <Button asChild className="gap-2 bg-blue-600 hover:bg-blue-700">
              <a href="#contact">
                Schedule Consult <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
          </div>
        </div>
      </header>

      <main>
        {/* Hero Section Re-aligned away from "simple storage software" */}
        <section
          className="relative flex min-h-[80vh] items-center overflow-hidden bg-cover bg-center"
          style={{
            backgroundImage:
              "linear-gradient(90deg, rgba(15, 23, 42, 0.95), rgba(15, 23, 42, 0.8), rgba(15, 23, 42, 0.4)), url('/New%20folder/Contract-Management.jpeg')",
          }}
        >
          <div className="container py-20 text-white">
            <div className="max-w-4xl">
              <p className="mb-4 inline-flex items-center gap-2 rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-blue-400">
                <Scale className="h-4 w-4" />
                Infrastructure Correspondence & Claims Control
              </p>
              <h1 className="text-4xl font-extrabold leading-tight tracking-tight md:text-6xl text-white">
                Defend Your Contractual Margins. Control Your Records.
              </h1>
              <p className="mt-6 max-w-2xl text-lg md:text-xl leading-8 text-slate-300">
                Stop treating project letters like simple data storage. ContraClaim pairs a precision, multi-package tracking platform with elite contract engineering expertise to eliminate missed responses and bulletproof your claims.
              </p>
              <div className="mt-10 flex flex-col gap-4 sm:flex-row">
                <Button asChild size="lg" className="gap-2 bg-blue-600 hover:bg-blue-700 text-white">
                  <a href="#solutions">
                    Explore Commercial Lines <ArrowRight className="h-5 w-5" />
                  </a>
                </Button>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="border-slate-500 text-white bg-white/5 hover:bg-white hover:text-slate-950 transition-colors"
                >
                  <a href="#pilot">Review 90-Day Pilot Offer</a>
                </Button>
              </div>
            </div>
          </div>
        </section>

        {/* Quick Value Metrics Ribbon */}
        <section className="border-b border-slate-200 bg-slate-50">
          <div className="container grid gap-4 py-6 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { icon: Clock3, label: "Zero Time-Bar Exposures" },
              { icon: UploadCloud, label: "Relational Package Tagging" },
              { icon: ShieldAlert, label: "Claim Leakage Isolation" },
              { icon: FileText, label: "Audit-Grade Chronologies" },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-3 rounded-md bg-white p-4 shadow-sm border border-slate-100">
                <item.icon className="h-5 w-5 text-blue-600" />
                <span className="text-sm font-bold text-slate-700">{item.label}</span>
              </div>
            ))}
          </div>
        </section>

        {/* Features Modules */}
        <section id="features" className="container py-24">
          <div className="max-w-3xl">
            <p className="text-xs font-bold uppercase tracking-widest text-blue-600">
              Platform Architecture
            </p>
            <h2 className="mt-3 text-3xl font-extrabold tracking-tight text-slate-900 md:text-4xl">
              Engineered Specially for Heavy Construction & Engineering Risks
            </h2>
            <p className="mt-4 text-lg leading-8 text-slate-600">
              Unlike generic business cloud storage frameworks, ContraClaim is calibrated to manage the exact interconnected dynamics of project execution, delay tracking, and formal employer interactions.
            </p>
          </div>

          <div className="mt-12 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
            {features.map((feature) => (
              <Card key={feature.title} className="border-slate-200 shadow-sm hover:shadow-md transition-shadow">
                <CardContent className="p-6">
                  <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                    <feature.icon className="h-5 w-5" />
                  </div>
                  <h3 className="text-md font-bold text-slate-900">
                    {feature.title}
                  </h3>
                  <p className="mt-2.5 text-xs sm:text-sm leading-6 text-slate-600">
                    {feature.description}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>

        {/* Commercial Lines Structure Section */}
        <section id="solutions" className="bg-slate-50 border-y border-slate-200 py-24">
          <div className="container">
            <div className="mx-auto max-w-3xl text-center mb-16">
              <p className="text-xs font-bold uppercase tracking-widest text-blue-600">Commercial Framework</p>
              <h2 className="mt-3 text-3xl font-extrabold tracking-tight text-slate-900 md:text-4xl">Select Your Operational Mode</h2>
              <p className="mt-4 text-md text-slate-600">Deploy our platform infrastructure or leverage a completely managed contract correspondence workspace.</p>
            </div>

            <div className="grid gap-8 lg:grid-cols-2 max-w-5xl mx-auto">
              {commercialLines.map((line) => (
                <div 
                  key={line.title} 
                  className={`relative flex flex-col justify-between p-8 rounded-xl bg-white border shadow-sm transition-transform ${
                    line.premium ? 'border-blue-600 ring-1 ring-blue-600/30' : 'border-slate-200'
                  }`}
                >
                  <div>
                    <div className="flex items-center justify-between gap-4 mb-4">
                      <span className={`px-2.5 py-1 text-xs font-bold uppercase tracking-wider rounded ${
                        line.premium ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-700'
                      }`}>
                        {line.badge}
                      </span>
                    </div>
                    <h3 className="text-2xl font-bold text-slate-900">{line.title}</h3>
                    <p className="mt-3 text-slate-600 text-sm leading-relaxed">{line.description}</p>
                    <ul className="mt-6 space-y-3">
                      {line.features.map((feat) => (
                        <li key={feat} className="flex items-start gap-2.5 text-sm text-slate-700">
                          <CheckCircle2 className="h-4 w-4 mt-0.5 text-blue-600 flex-shrink-0" />
                          <span>{feat}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="mt-8 pt-4 border-t border-slate-100">
                    <Button asChild className={`w-full ${line.premium ? 'bg-blue-600 hover:bg-blue-700' : 'bg-slate-900 hover:bg-slate-800'}`}>
                      <a href={line.href}>{line.cta}</a>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Benefits Panel */}
        <section id="benefits" className="bg-slate-900 py-24 text-white">
          <div className="container grid gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:items-center">
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-blue-400">
                Risk Containment
              </p>
              <h2 className="mt-3 text-3xl font-extrabold tracking-tight md:text-4xl text-white">
                A Unified Single Source of Truth for Claims Defense
              </h2>
              <p className="mt-4 text-base leading-8 text-slate-400">
                Infrastructure contracts are won or lost on documentation history. ContraClaim shields cash-flow positions by ensuring your site records are system-linked, readily retrievable, and auditable.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {benefits.map((benefit) => (
                <div key={benefit} className="rounded-lg border border-slate-800 bg-slate-950 p-6">
                  <ShieldCheck className="mb-4 h-6 w-6 text-emerald-400" />
                  <p className="text-sm leading-relaxed text-slate-300 font-medium">{benefit}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Workflow Mechanics */}
        <section id="how-it-works" className="container py-24">
          <div className="mx-auto max-w-3xl text-center">
            <p className="text-xs font-bold uppercase tracking-widest text-blue-600">
              Strategic Onboarding
            </p>
            <h2 className="mt-3 text-3xl font-extrabold tracking-tight text-slate-900 md:text-4xl">
              Systematic Deployment Sequence
            </h2>
          </div>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {steps.map((step, index) => (
              <div key={step.title} className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm relative">
                <div className="mb-4 flex h-8 w-8 items-center justify-center rounded-md bg-blue-50 text-xs font-bold text-blue-600">
                  0{index + 1}
                </div>
                <h3 className="text-lg font-bold text-slate-900">{step.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Strategic Market Entry Offer Box (90-Day Pilot) */}
        <section id="pilot" className="bg-blue-50 border-y border-blue-100 py-20">
          <div className="container max-w-4xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-blue-100 text-blue-800 text-xs font-bold uppercase tracking-wider mb-4">
              <CalendarDays className="h-4 w-4" /> Market Entry Framework
            </div>
            <h2 className="text-3xl font-extrabold tracking-tight text-slate-900">The 90-Day Enterprise Pilot Program</h2>
            <p className="mt-4 text-md text-slate-600 max-w-2xl mx-auto">
              Lower initial friction. Fast-track setup on a single major package or active project milestone to experience combined software-plus-drafting defense.
            </p>
            <div className="mt-8 grid gap-4 text-left bg-white p-6 sm:p-8 rounded-xl border border-blue-200 shadow-sm sm:grid-cols-2">
              <div className="space-y-3">
                <h4 className="font-bold text-slate-900 text-sm uppercase tracking-wider text-blue-600">Program Inclusions:</h4>
                <ul className="space-y-2 text-sm text-slate-700">
                  <li className="flex items-center gap-2">✔ Standard DMS setup for 1 project</li>
                  <li className="flex items-center gap-2">✔ Content ingestion of active letter backlog</li>
                  <li className="flex items-center gap-2">✔ Up to 15 expert-drafted letters / mo</li>
                </ul>
              </div>
              <div className="space-y-3">
                <h4 className="font-bold text-slate-900 text-sm uppercase tracking-wider text-blue-600">Analytical Deliverables:</h4>
                <ul className="space-y-2 text-sm text-slate-700">
                  <li className="flex items-center gap-2">✔ Initial claims event chronology map</li>
                  <li className="flex items-center gap-2">✔ Monthly contractual exposure audit report</li>
                  <li className="flex items-center gap-2">✔ Safe dashboard seats for 5–10 active users</li>
                </ul>
              </div>
            </div>
            <p className="mt-6 text-xs text-slate-500 italic">Following program conclusion, accounts seamlessly convert to standard DMS subscriptions, monthly retainers, or a full Contract Correspondence Desk.</p>
          </div>
        </section>

        {/* Contact Form Section */}
        <section id="contact" className="bg-white py-24">
          <div className="container grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-blue-600">
                Commercial Intake
              </p>
              <h2 className="mt-3 text-3xl font-extrabold tracking-tight text-slate-900 md:text-4xl">
                Initiate Project Assessment
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-slate-600">
                Connect with our commercial solutions team to map out your infrastructure package layout, review legacy file migration steps, or structure a custom expert drafting retainer.
              </p>
              <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-5">
                <div className="flex items-start gap-3">
                  <FileSearch className="mt-1 h-5 w-5 text-blue-600 flex-shrink-0" />
                  <p className="text-xs sm:text-sm leading-6 text-slate-600">
                    <strong>Notice:</strong> This channel routes inquiries directly to our secure enterprise onboarding inbox. To preserve confidentiality parameters, do not include specific live case details, passkeys, or sensitive dispute-sensitive documents within this layout.
                  </p>
                </div>
              </div>
            </div>

            <Card className="border-slate-200 shadow-sm">
              <CardContent className="p-6">
                <form className="space-y-5" onSubmit={handleContactSubmit}>
                  {contactStatus.type !== "idle" && (
                    <Alert
                      variant={contactStatus.type === "error" ? "destructive" : "default"}
                      role="status"
                      aria-live="polite"
                    >
                      <AlertDescription>{contactStatus.message}</AlertDescription>
                    </Alert>
                  )}

                  <div className="grid gap-5 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="contact-name">Full Name</Label>
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
                      <Label htmlFor="contact-email">Corporate Email</Label>
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
                      <Label htmlFor="contact-organization">Enterprise Entity</Label>
                      <Input
                        id="contact-organization"
                        name="organization"
                        autoComplete="organization"
                        maxLength={160}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="contact-phone">Contact Number</Label>
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
                    <Label htmlFor="contact-message">Project Outline or Scope Requirements</Label>
                    <Textarea
                      id="contact-message"
                      name="message"
                      placeholder="Specify if you are inquiring about the 90-Day Pilot, Platform SaaS, or Managed Expert Drafting Services..."
                      minLength={10}
                      maxLength={4000}
                      required
                      className="min-h-32 resize-y"
                    />
                  </div>

                  <Button type="submit" size="lg" className="w-full sm:w-auto bg-blue-600 hover:bg-blue-700" disabled={submittingContact}>
                    {submittingContact ? "Transmitting..." : "Submit Inquiry"}
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        </section>

        {/* Pre-Footer Action Trigger */}
        <section className="border-t border-slate-200 bg-slate-50 py-16">
          <div className="container flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <h2 className="text-2xl font-bold text-slate-900 md:text-3xl">
                Access Active Project Instances
              </h2>
              <p className="mt-2 text-slate-600 text-sm sm:text-base">
                Authorized project managers, independent engineers, and contract executives can log directly into their secured package instances below.
              </p>
            </div>
            <Button asChild size="lg" className="gap-2 bg-slate-900 hover:bg-slate-800">
              <Link to="/login">
                Workspace Secure Login <ArrowRight className="h-5 w-5" />
              </Link>
            </Button>
          </div>
        </section>
      </main>

      <footer className="bg-white border-t border-slate-100">
        <div className="container flex flex-col gap-4 py-8 text-xs sm:text-sm text-slate-500 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <img src="/page.png" alt="" className="h-8 w-8 rounded-md object-cover" aria-hidden="true" />
            <span className="font-bold text-slate-700 tracking-tight">ContraClaim Platform</span>
          </div>
          <div className="flex flex-wrap gap-5 font-medium">
            <a href="#features" className="hover:text-blue-600 transition-colors">
              Capabilities
            </a>
            <a href="#solutions" className="hover:text-blue-600 transition-colors">
              Commercial Lines
            </a>
            <a href="#pilot" className="hover:text-blue-600 transition-colors">
              90-Day Pilot
            </a>
            <Link to="/login" className="hover:text-blue-600 transition-colors">
              Sign In
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;