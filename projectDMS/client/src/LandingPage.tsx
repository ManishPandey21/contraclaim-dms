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
  Search,
  ShieldCheck,
  UploadCloud,
  Users,
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
    icon: FileText,
    title: "Letters and document library",
    description:
      "Upload, classify, search, view, share, and download project documents with metadata, tags, references, and structured folder browsing.",
  },
  {
    icon: MessageSquareText,
    title: "Contract intelligence",
    description:
      "Upload contracts, search clauses, and use the contract Q&A workspace backed by the contract upload and search flows in the application.",
  },
  {
    icon: MailCheck,
    title: "Letter drafting workflow",
    description:
      "Manage correspondence from input and strategic planning through drafting, review, approval, completion, templates, and quality tracking.",
  },
  {
    icon: Building2,
    title: "Organizations and projects",
    description:
      "Separate work by organization and project so teams can keep documents, users, parties, and correspondence aligned to the right matter.",
  },
  {
    icon: Users,
    title: "Stakeholders and email groups",
    description:
      "Maintain parties, representatives, concerns, and email groups used by project teams and correspondence workflows.",
  },
  {
    icon: ShieldCheck,
    title: "Role-based access control",
    description:
      "Control user, role, permission, project, document, folder, and download access through the existing RBAC screens and backend checks.",
  },
  {
    icon: BarChart3,
    title: "Dashboards and reporting",
    description:
      "Track letter status, recent activity, organizational summaries, reports, analytics, and system health from dedicated dashboards.",
  },
  {
    icon: Bell,
    title: "Notifications and activity",
    description:
      "Use notification and sharing flows to keep teams aware of document activity, input requests, and workflow progress.",
  },
];

const benefits = [
  "Keeps letters, contracts, projects, parties, and references in one controlled workspace.",
  "Reduces manual tracking through status dashboards, workflow stages, and searchable metadata.",
  "Supports governance with role permissions, project scoping, audit-oriented services, and secure file access.",
  "Gives teams a consistent place to upload, review, respond, report, and retrieve project records.",
];

const steps = [
  {
    title: "Structure the workspace",
    description:
      "Set up organizations, projects, users, roles, parties, and email groups before documents enter the system.",
  },
  {
    title: "Upload and organize records",
    description:
      "Add letters and contracts, enrich them with metadata, and browse them through search, libraries, tags, and folders.",
  },
  {
    title: "Work through decisions",
    description:
      "Use drafting, review, approval, Q&A, reporting, and notifications to move claims and correspondence forward.",
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
        message: "Your message has been sent. The ContraClaim team will contact you shortly.",
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
          "Unable to send your message right now. Please try again later.",
      });
    } finally {
      setSubmittingContact(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-docsumo-text">
      <header className="sticky top-0 z-50 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="container flex h-16 items-center justify-between gap-6">
          <Link to="/" className="flex items-center gap-3" aria-label="ContraClaim DMS home">
            <img src="/contraclaim2.png" alt="ContraClaim DMS" className="h-9 w-auto" />
          </Link>
          <nav className="hidden items-center gap-6 text-sm font-medium text-slate-600 md:flex">
            <a href="#features" className="hover:text-docsumo-blue">
              Features
            </a>
            <a href="#benefits" className="hover:text-docsumo-blue">
              Benefits
            </a>
            <a href="#how-it-works" className="hover:text-docsumo-blue">
              How it works
            </a>
            <a href="#contact" className="hover:text-docsumo-blue">
              Contact
            </a>
          </nav>
          <Button asChild className="gap-2">
            <Link to="/login">
              Login <ArrowRight className="h-4 w-4" />
            </Link>
          </Button>
        </div>
      </header>

      <main>
        <section
          className="relative flex min-h-[74vh] items-center overflow-hidden bg-cover bg-center"
          style={{
            backgroundImage:
              "linear-gradient(90deg, rgba(15, 23, 42, 0.88), rgba(15, 23, 42, 0.66), rgba(15, 23, 42, 0.34)), url('/New%20folder/Contract-Management.jpeg')",
          }}
        >
          <div className="container py-20 text-white">
            <div className="max-w-3xl">
              <p className="mb-4 inline-flex items-center gap-2 rounded-md border border-white/25 bg-white/10 px-3 py-2 text-sm font-medium text-white">
                <LockKeyhole className="h-4 w-4" />
                Secure document, claim, and contract workflows
              </p>
              <h1 className="text-4xl font-bold leading-tight md:text-6xl">
                ContraClaim DMS
              </h1>
              <p className="mt-5 max-w-2xl text-xl leading-8 text-slate-100">
                A controlled workspace for managing project correspondence,
                contracts, claims records, stakeholders, workflows, and reporting.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <Button asChild size="lg" className="gap-2 bg-docsumo-blue hover:bg-docsumo-darkBlue">
                  <Link to="/login">
                    Login to your workspace <ArrowRight className="h-5 w-5" />
                  </Link>
                </Button>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="border-white bg-white/10 text-white hover:bg-white hover:text-slate-900"
                >
                  <a href="#features">View platform features</a>
                </Button>
              </div>
            </div>
          </div>
        </section>

        <section className="border-b border-slate-200 bg-slate-50">
          <div className="container grid gap-4 py-6 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { icon: FolderTree, label: "Project folder structure" },
              { icon: Search, label: "Search and metadata filters" },
              { icon: UploadCloud, label: "Document and contract upload" },
              { icon: ShieldCheck, label: "RBAC protected access" },
            ].map((item) => (
              <div key={item.label} className="flex items-center gap-3 rounded-md bg-white p-4 shadow-sm">
                <item.icon className="h-5 w-5 text-docsumo-blue" />
                <span className="text-sm font-semibold text-slate-700">{item.label}</span>
              </div>
            ))}
          </div>
        </section>

        <section id="features" className="container py-20">
          <div className="max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-wider text-docsumo-blue">
              Platform Features
            </p>
            <h2 className="mt-3 text-3xl font-bold text-slate-900 md:text-4xl">
              Built around the actual ContraClaim DMS workflows
            </h2>
            <p className="mt-4 text-lg leading-8 text-slate-600">
              The landing page reflects the modules already present in the
              repository: documents, contracts, letters, projects, stakeholders,
              permissions, reporting, notifications, and system operations.
            </p>
          </div>

          <div className="mt-10 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
            {features.map((feature) => (
              <Card key={feature.title} className="border-slate-200 shadow-sm">
                <CardContent className="p-6">
                  <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-md bg-docsumo-blue/10 text-docsumo-blue">
                    <feature.icon className="h-6 w-6" />
                  </div>
                  <h3 className="text-lg font-semibold text-slate-900">
                    {feature.title}
                  </h3>
                  <p className="mt-3 text-sm leading-6 text-slate-600">
                    {feature.description}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>

        <section id="benefits" className="bg-slate-900 py-20 text-white">
          <div className="container grid gap-10 lg:grid-cols-[0.85fr_1.15fr] lg:items-start">
            <div>
              <p className="text-sm font-semibold uppercase tracking-wider text-sky-300">
                Operational Benefits
              </p>
              <h2 className="mt-3 text-3xl font-bold md:text-4xl">
                A single source of truth for claim correspondence and project records
              </h2>
              <p className="mt-4 text-lg leading-8 text-slate-300">
                ContraClaim DMS focuses on controlled records, workflow visibility,
                and secure collaboration for teams working with letters, contracts,
                references, stakeholders, and project documentation.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {benefits.map((benefit) => (
                <div key={benefit} className="rounded-md border border-white/10 bg-white/5 p-5">
                  <CheckCircle2 className="mb-4 h-6 w-6 text-docsumo-success" />
                  <p className="text-sm leading-6 text-slate-100">{benefit}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="how-it-works" className="container py-20">
          <div className="mx-auto max-w-3xl text-center">
            <p className="text-sm font-semibold uppercase tracking-wider text-docsumo-blue">
              How It Works
            </p>
            <h2 className="mt-3 text-3xl font-bold text-slate-900 md:text-4xl">
              From project setup to controlled retrieval
            </h2>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {steps.map((step, index) => (
              <div key={step.title} className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
                <div className="mb-5 flex h-10 w-10 items-center justify-center rounded-md bg-slate-100 text-sm font-bold text-docsumo-blue">
                  {index + 1}
                </div>
                <h3 className="text-lg font-semibold text-slate-900">{step.title}</h3>
                <p className="mt-3 text-sm leading-6 text-slate-600">{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="contact" className="border-y border-slate-200 bg-white py-20">
          <div className="container grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div>
              <p className="text-sm font-semibold uppercase tracking-wider text-docsumo-blue">
                Contact Us
              </p>
              <h2 className="mt-3 text-3xl font-bold text-slate-900 md:text-4xl">
                Talk to the ContraClaim DMS team
              </h2>
              <p className="mt-4 text-lg leading-8 text-slate-600">
                Send a message from this page and it will be delivered to the
                configured ContraClaim contact mailbox. The recipient is managed
                safely on the backend through environment configuration.
              </p>
              <div className="mt-6 rounded-md border border-slate-200 bg-slate-50 p-5">
                <div className="flex items-start gap-3">
                  <FileSearch className="mt-1 h-5 w-5 text-docsumo-blue" />
                  <p className="text-sm leading-6 text-slate-600">
                    Use this for product enquiries, onboarding support, and
                    workspace access questions. Do not include passwords or
                    confidential claim details in the contact form.
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

                  <Button type="submit" size="lg" className="w-full sm:w-auto" disabled={submittingContact}>
                    {submittingContact ? "Sending..." : "Send Message"}
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        </section>

        <section className="border-y border-slate-200 bg-slate-50 py-16">
          <div className="container flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <h2 className="text-2xl font-bold text-slate-900 md:text-3xl">
                Access your ContraClaim DMS workspace
              </h2>
              <p className="mt-3 text-slate-600">
                Use the existing authentication page to sign in with your issued
                credentials and continue to the secured application.
              </p>
            </div>
            <Button asChild size="lg" className="gap-2">
              <Link to="/login">
                Login <ArrowRight className="h-5 w-5" />
              </Link>
            </Button>
          </div>
        </section>
      </main>

      <footer className="bg-white">
        <div className="container flex flex-col gap-4 py-8 text-sm text-slate-500 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <img src="/page.png" alt="" className="h-8 w-8 rounded-md object-cover" aria-hidden="true" />
            <span className="font-semibold text-slate-700">ContraClaim DMS</span>
          </div>
          <div className="flex flex-wrap gap-5">
            <a href="#features" className="hover:text-docsumo-blue">
              Features
            </a>
            <a href="#benefits" className="hover:text-docsumo-blue">
              Benefits
            </a>
            <a href="#contact" className="hover:text-docsumo-blue">
              Contact
            </a>
            <Link to="/login" className="hover:text-docsumo-blue">
              Login
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
