import React from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import logo from "@/assets/logo.png";
import {
  FileText,
  Building2,
  Mail,
  FileSignature,
  Users,
  CheckSquare,
  BarChart3,
  Shield,
  Cloud,
  ArrowRight,
  Upload,
  Search,
  CheckCircle,
  Sparkles,
  Lock,
  Zap,
  Globe,
} from 'lucide-react';

const LandingPage = () => {
  const features = [
    {
      icon: <FileText className="h-10 w-10" />,
      title: "Document Management",
      description: "Upload, organize, and view documents with powerful search capabilities. Tag documents with metadata for easy retrieval and maintain complete version history.",
      color: "from-blue-500 to-cyan-500"
    },
    {
      icon: <Building2 className="h-10 w-10" />,
      title: "Organization & Project Management",
      description: "Multi-tenant architecture supporting multiple organizations and projects. Create hierarchical structures with custom short names and dedicated storage paths.",
      color: "from-purple-500 to-pink-500"
    },
    {
      icon: <Mail className="h-10 w-10" />,
      title: "Letter Workflow",
      description: "End-to-end letter processing with strategic planning, drafting, review, and approval stages. Track pendency and manage correspondence efficiently.",
      color: "from-orange-500 to-red-500"
    },
    {
      icon: <FileSignature className="h-10 w-10" />,
      title: "Contract Management",
      description: "Upload contracts and search through clauses with AI-powered analysis. Extract key terms, dates, and obligations automatically for better compliance.",
      color: "from-green-500 to-emerald-500"
    },
    {
      icon: <Users className="h-10 w-10" />,
      title: "Stakeholder Management",
      description: "Track and manage all parties, representatives, and their concerns effectively. Maintain comprehensive records of all stakeholder interactions and communications.",
      color: "from-indigo-500 to-violet-500"
    },
    {
      icon: <CheckSquare className="h-10 w-10" />,
      title: "Task Allocation",
      description: "Assign and track tasks across teams with priority management and deadlines. Get notifications for overdue items and monitor team productivity.",
      color: "from-teal-500 to-cyan-500"
    },
    {
      icon: <BarChart3 className="h-10 w-10" />,
      title: "Reports & Analytics",
      description: "Comprehensive reporting with customizable filters and interactive data visualization. Export reports in multiple formats and schedule automated reports.",
      color: "from-amber-500 to-orange-500"
    },
    {
      icon: <Shield className="h-10 w-10" />,
      title: "User Management & Permissions",
      description: "Role-based access control with granular permissions for secure collaboration. Define custom roles and manage user access at organization, project, and document levels.",
      color: "from-rose-500 to-pink-500"
    },
    {
      icon: <Cloud className="h-10 w-10" />,
      title: "Flexible Storage",
      description: "Support for Local, Amazon S3, Azure Blob, and Google Cloud Storage providers. Configure storage at organization or project level with seamless integration.",
      color: "from-sky-500 to-blue-500"
    },
  ];

  const steps = [
    {
      icon: <Building2 className="h-12 w-12" />,
      step: "01",
      title: "Set Up Organization",
      description: "Create your organization and configure projects with custom short names, storage paths, and team members."
    },
    {
      icon: <Upload className="h-12 w-12" />,
      step: "02",
      title: "Upload Documents",
      description: "Upload and organize documents into structured folders with rich metadata, tags, and custom attributes."
    },
    {
      icon: <Search className="h-12 w-12" />,
      step: "03",
      title: "Search & Collaborate",
      description: "Find documents instantly with powerful search and collaborate with your team through automated workflows."
    },
  ];

  const highlights = [
    {
      icon: <Sparkles className="h-6 w-6" />,
      title: "AI-Powered",
      description: "Smart document analysis"
    },
    {
      icon: <Lock className="h-6 w-6" />,
      title: "Enterprise Security",
      description: "256-bit encryption"
    },
    {
      icon: <Zap className="h-6 w-6" />,
      title: "Lightning Fast",
      description: "Sub-second search"
    },
    {
      icon: <Globe className="h-6 w-6" />,
      title: "Multi-Cloud",
      description: "Deploy anywhere"
    },
  ];

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-background/80 backdrop-blur-md border-b border-border">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <img src={logo} alt="ContraClaim Logo" className="h-10 w-auto" />
          </div>
          <nav className="hidden md:flex items-center gap-8">
            <a href="#features" className="text-muted-foreground hover:text-foreground transition-colors font-medium">Features</a>
            <a href="#how-it-works" className="text-muted-foreground hover:text-foreground transition-colors font-medium">How It Works</a>
            <a href="#about" className="text-muted-foreground hover:text-foreground transition-colors font-medium">About</a>
          </nav>
          <Link to="/login">
            <Button className="gap-2 shadow-lg hover:shadow-xl transition-shadow">
              Login <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
        </div>
      </header>

      {/* Hero Section */}
      <section className="pt-32 pb-20 px-4 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-primary/10 pointer-events-none" />
        <div className="absolute top-20 left-10 w-72 h-72 bg-primary/10 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute bottom-10 right-10 w-96 h-96 bg-primary/5 rounded-full blur-3xl pointer-events-none" />
        
        <div className="container mx-auto text-center relative z-10">
          <div className="inline-flex items-center gap-2 bg-primary/10 text-primary px-4 py-2 rounded-full text-sm font-medium mb-6 border border-primary/20">
            <CheckCircle className="h-4 w-4" />
            Trusted Document Management Solution
          </div>
          <h1 className="text-4xl md:text-6xl lg:text-7xl font-bold text-foreground mb-6 leading-tight">
            Streamline Your
            <span className="text-primary block bg-gradient-to-r from-primary to-primary/70 bg-clip-text">Document Management</span>
          </h1>
          <p className="text-xl text-muted-foreground max-w-3xl mx-auto mb-10 leading-relaxed">
            A comprehensive enterprise platform for managing documents, contracts, and correspondence 
            with powerful workflows, real-time collaboration tools, and advanced analytics for data-driven decisions.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-16">
            <Link to="/login">
              <Button size="lg" className="gap-2 text-lg px-10 shadow-lg hover:shadow-xl transition-all hover:scale-105">
                Get Started <ArrowRight className="h-5 w-5" />
              </Button>
            </Link>
            <a href="#features">
              <Button size="lg" variant="outline" className="text-lg px-10 hover:bg-primary/5">
                Explore Features
              </Button>
            </a>
          </div>

          {/* Highlights Bar */}
          <div className="flex flex-wrap justify-center gap-6 md:gap-12">
            {highlights.map((item, index) => (
              <div key={index} className="flex items-center gap-3 text-left">
                <div className="p-2 rounded-lg bg-primary/10 text-primary">
                  {item.icon}
                </div>
                <div>
                  <div className="font-semibold text-foreground text-sm">{item.title}</div>
                  <div className="text-xs text-muted-foreground">{item.description}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="py-24 px-4 bg-muted/30">
        <div className="container mx-auto">
          <div className="text-center mb-16">
            <span className="text-primary font-semibold text-sm uppercase tracking-wider">Features</span>
            <h2 className="text-3xl md:text-5xl font-bold text-foreground mt-2 mb-4">
              Powerful Features for Modern Teams
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Everything you need to manage documents, contracts, and correspondence in one unified platform.
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
            {features.map((feature, index) => (
              <Card key={index} className="group hover:shadow-2xl transition-all duration-500 hover:-translate-y-2 border-border/50 overflow-hidden relative">
                <div className={`absolute inset-0 bg-gradient-to-br ${feature.color} opacity-0 group-hover:opacity-5 transition-opacity duration-500`} />
                <CardContent className="p-8 relative">
                  <div className={`mb-6 p-3 rounded-xl bg-gradient-to-br ${feature.color} text-white w-fit shadow-lg group-hover:scale-110 transition-transform duration-300`}>
                    {feature.icon}
                  </div>
                  <h3 className="text-xl font-bold text-foreground mb-3">{feature.title}</h3>
                  <p className="text-muted-foreground leading-relaxed">{feature.description}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* How It Works Section */}
      <section id="how-it-works" className="py-24 px-4 relative">
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-primary/5 to-transparent pointer-events-none" />
        <div className="container mx-auto relative z-10">
          <div className="text-center mb-16">
            <span className="text-primary font-semibold text-sm uppercase tracking-wider">Process</span>
            <h2 className="text-3xl md:text-5xl font-bold text-foreground mt-2 mb-4">
              How It Works
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Get started in three simple steps and transform your document management
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-12 max-w-6xl mx-auto">
            {steps.map((step, index) => (
              <div key={index} className="relative text-center group">
                <div className="inline-flex items-center justify-center w-24 h-24 rounded-2xl bg-gradient-to-br from-primary to-primary/70 text-white mb-8 shadow-xl group-hover:scale-110 transition-transform duration-300">
                  {step.icon}
                </div>
                <div className="absolute -top-4 left-1/2 -translate-x-1/2 text-8xl font-bold text-primary/10 group-hover:text-primary/20 transition-colors">
                  {step.step}
                </div>
                <h3 className="text-2xl font-bold text-foreground mb-4">{step.title}</h3>
                <p className="text-muted-foreground leading-relaxed">{step.description}</p>
                {index < steps.length - 1 && (
                  <div className="hidden md:block absolute top-12 left-[60%] w-[80%] border-t-2 border-dashed border-primary/30" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* About Section */}
      <section id="about" className="py-24 px-4 bg-muted/30">
        <div className="container mx-auto max-w-5xl">
          <div className="text-center mb-12">
            <span className="text-primary font-semibold text-sm uppercase tracking-wider">About Us</span>
            <h2 className="text-3xl md:text-5xl font-bold text-foreground mt-2 mb-6">
              About ContraClaim DMS
            </h2>
            <p className="text-lg text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              ContraClaim DMS is a modern, enterprise-grade document management system designed for organizations 
              that need to handle complex document workflows, contract management, and correspondence 
              tracking. Built with security, scalability, and ease of use at its core, our platform 
              empowers teams to work more efficiently and make data-driven decisions.
            </p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8 mt-12">
            <div className="text-center p-6 rounded-2xl bg-background shadow-lg hover:shadow-xl transition-shadow">
              <div className="text-4xl font-bold text-primary mb-2">99.9%</div>
              <div className="text-muted-foreground font-medium">Uptime SLA</div>
            </div>
            <div className="text-center p-6 rounded-2xl bg-background shadow-lg hover:shadow-xl transition-shadow">
              <div className="text-4xl font-bold text-primary mb-2">256-bit</div>
              <div className="text-muted-foreground font-medium">AES Encryption</div>
            </div>
            <div className="text-center p-6 rounded-2xl bg-background shadow-lg hover:shadow-xl transition-shadow">
              <div className="text-4xl font-bold text-primary mb-2">24/7</div>
              <div className="text-muted-foreground font-medium">Expert Support</div>
            </div>
            <div className="text-center p-6 rounded-2xl bg-background shadow-lg hover:shadow-xl transition-shadow">
              <div className="text-4xl font-bold text-primary mb-2">Multi</div>
              <div className="text-muted-foreground font-medium">Cloud Ready</div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-24 px-4 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-r from-primary/10 via-primary/5 to-primary/10 pointer-events-none" />
        <div className="container mx-auto max-w-4xl text-center relative z-10">
          <h2 className="text-3xl md:text-5xl font-bold text-foreground mb-6">
            Ready to Transform Your Document Management?
          </h2>
          <p className="text-lg text-muted-foreground mb-10 max-w-2xl mx-auto">
            Join leading organizations that trust ContraClaim DMS for their document management needs. 
            Start your journey to streamlined workflows today.
          </p>
          <Link to="/login">
            <Button size="lg" className="gap-2 text-lg px-12 shadow-xl hover:shadow-2xl transition-all hover:scale-105">
              Login Now <ArrowRight className="h-5 w-5" />
            </Button>
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 border-t border-border bg-muted/20">
        <div className="container mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-2">
              <img src={logo} alt="ContraClaim Logo" className="h-10 w-auto" />
            </div>
            <div className="flex items-center gap-8 text-sm text-muted-foreground">
              <Link to="/login" className="hover:text-foreground transition-colors font-medium">Login</Link>
              <a href="#features" className="hover:text-foreground transition-colors font-medium">Features</a>
              <a href="#how-it-works" className="hover:text-foreground transition-colors font-medium">How It Works</a>
              <a href="#about" className="hover:text-foreground transition-colors font-medium">About</a>
            </div>
            <p className="text-sm text-muted-foreground">
              © {new Date().getFullYear()} ContraClaim DMS. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
