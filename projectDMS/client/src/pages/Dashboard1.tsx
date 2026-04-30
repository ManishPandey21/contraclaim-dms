import React, { useState } from 'react';
import { 
  FileText, MessageCircleQuestion, AlertCircle, Clock, CheckCircle2,
  BarChart3, Building, FolderArchive
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import StatusSummary from '@/components/dashboard/StatusSummary';
import StatusBreakdown from '@/components/dashboard/StatusBreakdown';
import ActionableItems from '@/components/dashboard/ActionableItems';
import OrganizationView from '@/components/dashboard/OrganizationView';
import ProjectView from '@/components/dashboard/ProjectView';
import DashboardHeader from '@/components/dashboard/DashboardHeader';

type LetterStatus = 'Received' | 'Input Required' | 'On Hold' | 'Under Review' | 
  'Under Process' | 'Replied' | 'Forwarded' | 'Completed' | 'Closed' | 
  'Reply Received' | 'No Reply Received' | 'Reply Overdue';

interface Letter {
  id: string;
  title: string;
  organization: string;
  project: string;
  status: LetterStatus;
  updatedAt: string;
  updatedBy: string;
  assignedTo: string;
}

interface Organization {
  id: string;
  name: string;
  totalLetters: number;
  replyOverdue: number;
  underReview: number;
}

interface Project {
  id: string;
  name: string;
  organization: string;
  totalLetters: number;
  inputRequired: number;
  closed: number;
}

const Dashboard = () => {
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [activeTab, setActiveTab] = useState<string>('overview');

  const letterStatuses: { status: LetterStatus; count: number; color: string }[] = [
    { status: 'Received', count: 38, color: '#3B82F6' },
    { status: 'Input Required', count: 24, color: '#F59E0B' },
    { status: 'Under Review', count: 18, color: '#8B5CF6' },
    { status: 'Under Process', count: 12, color: '#10B981' },
    { status: 'On Hold', count: 8, color: '#6B7280' },
    { status: 'Replied', count: 28, color: '#14B8A6' },
    { status: 'Forwarded', count: 6, color: '#EC4899' },
    { status: 'Completed', count: 32, color: '#22C55E' },
    { status: 'Closed', count: 46, color: '#64748B' },
    { status: 'Reply Received', count: 22, color: '#0EA5E9' },
    { status: 'No Reply Received', count: 12, color: '#EF4444' },
    { status: 'Reply Overdue', count: 16, color: '#DC2626' },
  ];

  const organizations: Organization[] = [
    { id: 'org1', name: 'Acme Corporation', totalLetters: 58, replyOverdue: 7, underReview: 5 },
    { id: 'org2', name: 'Globex Industries', totalLetters: 42, replyOverdue: 3, underReview: 8 },
    { id: 'org3', name: 'Umbrella LLC', totalLetters: 31, replyOverdue: 2, underReview: 3 },
    { id: 'org4', name: 'Initech Solutions', totalLetters: 27, replyOverdue: 4, underReview: 2 },
    { id: 'org5', name: 'Stark Enterprises', totalLetters: 36, replyOverdue: 0, underReview: 0 },
  ];

  const projects: Project[] = [
    { id: 'proj1', name: 'Annual Compliance Review', organization: 'org1', totalLetters: 24, inputRequired: 8, closed: 12 },
    { id: 'proj2', name: 'Legal Documentation', organization: 'org1', totalLetters: 18, inputRequired: 5, closed: 9 },
    { id: 'proj3', name: 'Quarterly Reports', organization: 'org2', totalLetters: 15, inputRequired: 3, closed: 11 },
    { id: 'proj4', name: 'Vendor Agreements', organization: 'org3', totalLetters: 22, inputRequired: 6, closed: 14 },
    { id: 'proj5', name: 'Tax Filings', organization: 'org4', totalLetters: 19, inputRequired: 7, closed: 8 },
    { id: 'proj6', name: 'Corporate Restructuring', organization: 'org5', totalLetters: 27, inputRequired: 9, closed: 15 },
  ];

  const recentLetters: Letter[] = [
    { 
      id: 'letter1', 
      title: 'Q3 Financial Statement Request', 
      organization: 'Acme Corporation', 
      project: 'Annual Compliance Review', 
      status: 'Input Required', 
      updatedAt: '2023-10-15T14:30:00Z',
      updatedBy: 'John Doe',
      assignedTo: 'Sarah Miller'
    },
    { 
      id: 'letter2', 
      title: 'Legal Contract Amendment', 
      organization: 'Globex Industries', 
      project: 'Legal Documentation', 
      status: 'Under Review', 
      updatedAt: '2023-10-14T10:15:00Z',
      updatedBy: 'Jane Smith',
      assignedTo: 'Mark Wilson'
    },
    { 
      id: 'letter3', 
      title: 'Vendor Payment Dispute', 
      organization: 'Umbrella LLC', 
      project: 'Vendor Agreements', 
      status: 'Reply Overdue', 
      updatedAt: '2023-10-10T09:45:00Z',
      updatedBy: 'Alice Johnson',
      assignedTo: 'Robert Brown'
    },
    { 
      id: 'letter4', 
      title: 'Tax Exemption Certificate', 
      organization: 'Initech Solutions', 
      project: 'Tax Filings', 
      status: 'Completed', 
      updatedAt: '2023-10-08T16:20:00Z',
      updatedBy: 'Emily Chen',
      assignedTo: 'David Wang'
    },
    { 
      id: 'letter5', 
      title: 'Board Meeting Minutes', 
      organization: 'Stark Enterprises', 
      project: 'Corporate Restructuring', 
      status: 'Closed', 
      updatedAt: '2023-10-05T11:30:00Z',
      updatedBy: 'Michael Scott',
      assignedTo: 'Jim Halpert'
    },
  ];

  const statusActivity = [
    { 
      id: 'activity1', 
      letter: 'Q3 Financial Statement Request', 
      previousStatus: 'Received', 
      newStatus: 'Input Required', 
      timestamp: '2023-10-15T14:30:00Z', 
      user: 'John Doe' 
    },
    { 
      id: 'activity2', 
      letter: 'Legal Contract Amendment', 
      previousStatus: 'Received', 
      newStatus: 'Under Review', 
      timestamp: '2023-10-14T10:15:00Z', 
      user: 'Jane Smith' 
    },
    { 
      id: 'activity3', 
      letter: 'Vendor Payment Dispute', 
      previousStatus: 'Under Process', 
      newStatus: 'Reply Overdue', 
      timestamp: '2023-10-10T09:45:00Z', 
      user: 'Alice Johnson' 
    },
  ];

  const taskAssignments = [
    { 
      id: 'task1', 
      title: 'Review Financial Statements', 
      letter: 'Q3 Financial Statement Request', 
      dueDate: '2023-10-20', 
      priority: 'High' 
    },
    { 
      id: 'task2', 
      title: 'Provide Input on Contract Terms', 
      letter: 'Legal Contract Amendment', 
      dueDate: '2023-10-18', 
      priority: 'Medium' 
    },
    { 
      id: 'task3', 
      title: 'Follow up on Payment Dispute', 
      letter: 'Vendor Payment Dispute', 
      dueDate: '2023-10-16', 
      priority: 'High' 
    },
  ];

  const totalLettersCount = letterStatuses.reduce((total, item) => total + item.count, 0);
  const inputRequiredCount = letterStatuses.find(s => s.status === 'Input Required')?.count || 0;
  const replyOverdueCount = letterStatuses.find(s => s.status === 'Reply Overdue')?.count || 0;
  const underReviewCount = letterStatuses.find(s => s.status === 'Under Review')?.count || 0;

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric', 
      year: 'numeric' 
    });
  };

  const getStatusIcon = (status: LetterStatus) => {
    switch (status) {
      case 'Input Required':
        return <MessageCircleQuestion className="text-amber-500" size={16} />;
      case 'Reply Overdue':
        return <AlertCircle className="text-red-600" size={16} />;
      case 'Under Review':
        return <Clock className="text-purple-600" size={16} />;
      case 'Completed':
        return <CheckCircle2 className="text-green-600" size={16} />;
      case 'Closed':
        return <CheckCircle2 className="text-gray-600" size={16} />;
      default:
        return <FileText className="text-blue-600" size={16} />;
    }
  };

  const getStatusBadge = (status: LetterStatus) => {
    let color = '';
    switch (status) {
      case 'Received':
        color = 'bg-blue-500';
        break;
      case 'Input Required':
        color = 'bg-amber-500';
        break;
      case 'On Hold':
        color = 'bg-gray-500';
        break;
      case 'Under Review':
        color = 'bg-purple-500';
        break;
      case 'Under Process':
        color = 'bg-emerald-500';
        break;
      case 'Replied':
        color = 'bg-teal-500';
        break;
      case 'Forwarded':
        color = 'bg-pink-500';
        break;
      case 'Completed':
        color = 'bg-green-500';
        break;
      case 'Closed':
        color = 'bg-gray-500';
        break;
      case 'Reply Received':
        color = 'bg-sky-500';
        break;
      case 'No Reply Received':
        color = 'bg-red-400';
        break;
      case 'Reply Overdue':
        color = 'bg-red-600';
        break;
      default:
        color = 'bg-gray-500';
    }
    return <Badge className={color}>{status}</Badge>;
  };

  const filteredProjects = selectedOrg 
    ? projects.filter(project => project.organization === selectedOrg)
    : projects;

  const selectedOrgDetails = selectedOrg 
    ? organizations.find(org => org.id === selectedOrg)
    : null;

  const selectedProjectDetails = selectedProject
    ? projects.find(project => project.id === selectedProject)
    : null;

  return (
    <div className="space-y-6 animate-fade-in">
      <DashboardHeader 
        letterStatuses={letterStatuses}
        statusFilter={statusFilter}
        setStatusFilter={setStatusFilter}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
      />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid grid-cols-3 w-full">
          <TabsTrigger value="overview">
            <BarChart3 className="mr-2 h-4 w-4" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="organizations">
            <Building className="mr-2 h-4 w-4" />
            Organizations
          </TabsTrigger>
          <TabsTrigger value="projects">
            <FolderArchive className="mr-2 h-4 w-4" />
            Projects
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-6">
          <StatusSummary
            totalLettersCount={totalLettersCount}
            inputRequiredCount={inputRequiredCount}
            replyOverdueCount={replyOverdueCount}
            underReviewCount={underReviewCount}
          />

          <StatusBreakdown
            letterStatuses={letterStatuses}
            statusActivity={statusActivity}
            formatDate={formatDate}
          />

          <ActionableItems
            letters={recentLetters}
            formatDate={formatDate}
            getStatusIcon={getStatusIcon}
          />
        </TabsContent>

        <TabsContent value="organizations" className="space-y-6">
          <OrganizationView
            organizations={organizations}
            projects={projects}
            letterStatuses={letterStatuses}
            totalLettersCount={totalLettersCount}
            selectedOrg={selectedOrg}
            setSelectedOrg={setSelectedOrg}
            selectedOrgDetails={selectedOrgDetails}
            filteredProjects={filteredProjects}
            setSelectedProject={setSelectedProject}
          />
        </TabsContent>

        <TabsContent value="projects" className="space-y-6">
          <ProjectView
            projects={projects}
            organizations={organizations}
            letterStatuses={letterStatuses}
            recentLetters={recentLetters}
            totalLettersCount={totalLettersCount}
            selectedProject={selectedProject}
            setSelectedProject={setSelectedProject}
            selectedProjectDetails={selectedProjectDetails}
            formatDate={formatDate}
            getStatusBadge={getStatusBadge}
            getStatusIcon={getStatusIcon}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default Dashboard;
