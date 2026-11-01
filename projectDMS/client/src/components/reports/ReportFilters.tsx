import React from 'react';
import { format } from 'date-fns';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Calendar } from 'lucide-react';
import { Calendar as CalendarComponent } from '@/components/ui/calendar';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

interface Organization {
  id: string;
  name: string;
}

interface Project {
  id: string;
  name: string;
  organizationId: string;
}

interface ReportFiltersProps {
  dateRange: 'all' | 'week' | 'fortnight' | 'month' | '3months' | 'custom';
  startDate?: Date;
  endDate?: Date;
  onDateRangeChange: (value: string) => void;
  onStartDateChange: (date?: Date) => void;
  onEndDateChange: (date?: Date) => void;
  selectedOrganization: string;
  setSelectedOrganization: (organizationId: string) => void;
  selectedProject: string;
  setSelectedProject: (projectId: string) => void;
  organizations: Organization[];
  projects: Project[];
}

export const ReportFilters: React.FC<ReportFiltersProps> = ({
  dateRange,
  startDate,
  endDate,
  onDateRangeChange,
  onStartDateChange,
  onEndDateChange,
  selectedOrganization,
  setSelectedOrganization,
  selectedProject,
  setSelectedProject,
  organizations,
  projects
}) => {
  // Format date for display
  const formatDateStr = (date: Date | undefined) => {
    return date ? format(date, 'PP') : 'Select date';
  };

  // Filter projects based on selected organization
  const filteredProjects = projects.filter(
    project => selectedOrganization === '' || project.organizationId === selectedOrganization
  );

  return (
    <div className="space-y-4">
      {/* Organization & Project Selection */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Organization</Label>
          <Select value={selectedOrganization} onValueChange={setSelectedOrganization}>
            <SelectTrigger>
              <SelectValue placeholder="All Organizations" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Organizations</SelectItem>
              {organizations.map(org => (
                <SelectItem key={org.id} value={org.id}>{org.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label>Project</Label>
          <Select
            value={selectedProject}
            onValueChange={setSelectedProject}
            disabled={filteredProjects.length === 0}
          >
            <SelectTrigger>
              <SelectValue placeholder="All Projects" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Projects</SelectItem>
              {filteredProjects.map(project => (
                <SelectItem key={project.id} value={project.id}>{project.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Date Range Selection */}
      <div className="space-y-2">
        <Label>Date Range</Label>
        <div className="flex flex-wrap gap-4 items-center">
          <Select value={dateRange} onValueChange={onDateRangeChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Select period" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Time</SelectItem>
              <SelectItem value="week">Last Week</SelectItem>
              <SelectItem value="fortnight">Last Fortnight</SelectItem>
              <SelectItem value="month">Last Month</SelectItem>
              <SelectItem value="3months">Last 3 Months</SelectItem>
              <SelectItem value="custom">Custom Range</SelectItem>
            </SelectContent>
          </Select>

          {dateRange === 'custom' && (
            <div className="flex flex-wrap gap-4">
              <div>
                <Label className="text-xs">Start Date</Label>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="w-[180px] justify-start">
                      <Calendar className="mr-2 h-4 w-4" />
                      {formatDateStr(startDate)}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <CalendarComponent
                      mode="single"
                      selected={startDate}
                      onSelect={onStartDateChange}
                      initialFocus
                      className="p-3 pointer-events-auto"
                    />
                  </PopoverContent>
                </Popover>
              </div>

              <div>
                <Label className="text-xs">End Date</Label>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="w-[180px] justify-start">
                      <Calendar className="mr-2 h-4 w-4" />
                      {formatDateStr(endDate)}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <CalendarComponent
                      mode="single"
                      selected={endDate}
                      onSelect={onEndDateChange}
                      initialFocus
                      className="p-3 pointer-events-auto"
                    />
                  </PopoverContent>
                </Popover>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
