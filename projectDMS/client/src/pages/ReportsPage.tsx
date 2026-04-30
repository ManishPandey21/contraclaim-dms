import React, { useState } from 'react';
import { 
  FileText, 
  Calendar, 
  Download, 
  List, 
  Filter, 
  ChevronDown,
  BarChart
} from 'lucide-react';
import { format } from 'date-fns';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Calendar as CalendarComponent } from '@/components/ui/calendar';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { toast } from 'sonner';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ReportPreview } from '@/components/reports/ReportPreview';
import { ReportFilters } from '@/components/reports/ReportFilters';
import { ColumnSelector } from '@/components/reports/ColumnSelector';
import { DownloadOptions } from '@/components/reports/DownloadOptions';

// Types
interface Report {
  id: string;
  name: string;
  description: string;
  icon: React.ReactNode;
  category: 'letters' | 'documents' | 'tasks';
}

interface ReportData {
  id: string;
  documentId: string;
  name: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  owner: string;
  type: string;
  organization: string;
  project: string;
}

const ReportsAnalyticsPage = () => {
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [dateRange, setDateRange] = useState<'week' | 'fortnight' | 'month' | '3months' | 'custom'>('week');
  const [startDate, setStartDate] = useState<Date | undefined>(new Date());
  const [endDate, setEndDate] = useState<Date | undefined>(new Date());
  const [selectedColumns, setSelectedColumns] = useState<string[]>([
    'name', 'status', 'createdAt', 'updatedAt', 'owner', 'type', 'organization', 'project'
  ]);
  const [previewData, setPreviewData] = useState<ReportData[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);

  // Available reports
  const reports: Report[] = [
    {
      id: '1',
      name: 'Letter Status Summary',
      description: 'Overview of all letters by status',
      icon: <FileText className="h-5 w-5" />,
      category: 'letters'
    },
    {
      id: '2',
      name: 'Document Processing Time',
      description: 'Average time for document processing by type',
      icon: <FileText className="h-5 w-5" />,
      category: 'documents'
    },
    {
      id: '3',
      name: 'Task Completion Rate',
      description: 'Tasks completed vs outstanding by assignee',
      icon: <FileText className="h-5 w-5" />,
      category: 'tasks'
    },
    {
      id: '4',
      name: 'Upload Analytics',
      description: 'Documents uploaded by organization and type',
      icon: <FileText className="h-5 w-5" />,
      category: 'documents'
    },
    {
      id: '5',
      name: 'Letter Approval Workflow',
      description: 'Time spent in each approval stage',
      icon: <FileText className="h-5 w-5" />,
      category: 'letters'
    }
  ];

  // Available columns for selection
  const availableColumns = [
    { id: 'name', label: 'Name' },
    { id: 'status', label: 'Status' },
    { id: 'createdAt', label: 'Created Date' },
    { id: 'updatedAt', label: 'Updated Date' },
    { id: 'owner', label: 'Owner' },
    { id: 'type', label: 'Type' },
    { id: 'organization', label: 'Organization' },
    { id: 'project', label: 'Project' }
  ];

  // Handle date range selection
  const handleDateRangeChange = (value: string) => {
    setDateRange(value as any);
    
    const today = new Date();
    let start = new Date();
    
    switch(value) {
      case 'week':
        start.setDate(today.getDate() - 7);
        break;
      case 'fortnight':
        start.setDate(today.getDate() - 14);
        break;
      case 'month':
        start.setMonth(today.getMonth() - 1);
        break;
      case '3months':
        start.setMonth(today.getMonth() - 3);
        break;
      case 'custom':
        // Don't change dates, allow user to select
        return;
    }
    
    setStartDate(start);
    setEndDate(today);
  };

  // Handle column selection
  const handleColumnToggle = (columnId: string) => {
    setSelectedColumns(prevSelected => 
      prevSelected.includes(columnId)
        ? prevSelected.filter(col => col !== columnId)
        : [...prevSelected, columnId]
    );
  };

  // Generate report preview
  const generatePreview = () => {
    if (!selectedReport) {
      toast.error("Please select a report type");
      return;
    }
    
    if (!startDate || !endDate) {
      toast.error("Please select a valid date range");
      return;
    }
    
    setIsGenerating(true);
    
    // Simulate API call to fetch report data
    setTimeout(() => {
      // Sample data - in a real app, this would come from an API
      const sampleData: ReportData[] = Array.from({ length: 10 }, (_, i) => ({
        id: doc-${i+1},
        documentId: DOC-2023-${1000 + i},
        name: Sample Document ${i+1},
        status: ['Draft', 'Under Review', 'Approved', 'Sent', 'Archived'][Math.floor(Math.random() * 5)],
        createdAt: new Date(2023, Math.floor(Math.random() * 12), Math.floor(Math.random() * 28) + 1).toISOString(),
        updatedAt: new Date(2023, Math.floor(Math.random() * 12), Math.floor(Math.random() * 28) + 1).toISOString(),
        owner: ['John Doe', 'Jane Smith', 'Robert Johnson', 'Lisa Chen'][Math.floor(Math.random() * 4)],
        type: ['Contract', 'Invoice', 'Proposal', 'Correspondence'][Math.floor(Math.random() * 4)],
        organization: ['Acme Corp', 'TechSolutions Inc', 'Global Enterprises'][Math.floor(Math.random() * 3)],
        project: ['Downtown Office', 'Riverside Development', 'Shopping Mall Renovation'][Math.floor(Math.random() * 3)]
      }));
      
      setPreviewData(sampleData);
      setIsGenerating(false);
      toast.success("Report preview generated successfully");
    }, 1500);
  };

  // Download report
  const downloadReport = (format: 'csv' | 'pdf') => {
    if (previewData.length === 0) {
      toast.error("Please generate a report preview first");
      return;
    }
    
    toast.success(Downloading report in ${format.toUpperCase()} format);
    
    // In a real app, this would call an API endpoint to generate and download the file
    console.log(Downloading ${format} report with columns: ${selectedColumns.join(', ')});
    console.log(Date range: ${startDate?.toISOString()} to ${endDate?.toISOString()});
  };

  // Format date for display
  const formatDate = (date: Date | undefined) => {
    return date ? format(date, 'PP') : '';
  };

  return (
    <div className="container mx-auto p-6">
      <div className="flex flex-col gap-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <BarChart className="h-6 w-6 text-docsumo-blue" />
              Reports & Analytics
            </h1>
            <p className="text-muted-foreground">Generate and download detailed reports</p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Reports Selection */}
          <div className="lg:col-span-1">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Available Reports</CardTitle>
                <CardDescription>Select a report to generate</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <ul className="divide-y">
                  {reports.map(report => (
                    <li key={report.id}>
                      <button
                        className={w-full px-4 py-3 text-left flex items-center gap-3 hover:bg-slate-50 transition-colors ${selectedReport?.id === report.id ? 'bg-blue-50 text-blue-700' : ''}}
                        onClick={() => setSelectedReport(report)}
                      >
                        <div className="flex-shrink-0 rounded-md bg-blue-100 p-2">
                          {report.icon}
                        </div>
                        <div>
                          <h3 className="text-sm font-medium">{report.name}</h3>
                          <p className="text-xs text-muted-foreground">{report.description}</p>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>

          {/* Report Configuration */}
          <div className="lg:col-span-3">
            <Card className="h-full">
              <CardHeader>
                <CardTitle className="text-lg">
                  {selectedReport ? selectedReport.name : 'Report Configuration'}
                </CardTitle>
                <CardDescription>
                  {selectedReport 
                    ? selectedReport.description 
                    : 'Select a report from the list to configure and generate'}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {selectedReport && (
                  <>
                    {/* Date Range Selection */}
                    <div className="space-y-2">
                      <Label>Date Range</Label>
                      <div className="flex flex-wrap gap-4 items-center">
                        <Select value={dateRange} onValueChange={handleDateRangeChange}>
                          <SelectTrigger className="w-[180px]">
                            <SelectValue placeholder="Select period" />
                          </SelectTrigger>
                          <SelectContent>
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
                                    {startDate ? formatDate(startDate) : "Select date"}
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-auto p-0" align="start">
                                  <CalendarComponent
                                    mode="single"
                                    selected={startDate}
                                    onSelect={setStartDate}
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
                                    {endDate ? formatDate(endDate) : "Select date"}
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-auto p-0" align="start">
                                  <CalendarComponent
                                    mode="single"
                                    selected={endDate}
                                    onSelect={setEndDate}
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
                    
                    {/* Column Selection */}
                    <div className="space-y-2">
                      <Label>Select Columns to Include</Label>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        {availableColumns.map(column => (
                          <div key={column.id} className="flex items-center space-x-2">
                            <Checkbox 
                              id={column-${column.id}} 
                              checked={selectedColumns.includes(column.id)}
                              onCheckedChange={() => handleColumnToggle(column.id)}
                            />
                            <label 
                              htmlFor={column-${column.id}}
                              className="text-sm cursor-pointer"
                            >
                              {column.label}
                            </label>
                          </div>
                        ))}
                      </div>
                    </div>
                    
                    {/* Actions */}
                    <div className="pt-4 flex flex-wrap gap-3 justify-between">
                      <Button onClick={generatePreview} disabled={isGenerating}>
                        {isGenerating ? 'Generating...' : 'Generate Preview'}
                      </Button>
                      
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          onClick={() => downloadReport('csv')}
                          disabled={previewData.length === 0}
                        >
                          <Download className="mr-2 h-4 w-4" />
                          Download CSV
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => downloadReport('pdf')}
                          disabled={previewData.length === 0}
                        >
                          <Download className="mr-2 h-4 w-4" />
                          Download PDF
                        </Button>
                      </div>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        </div>

        {/* Report Preview */}
        {previewData.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Report Preview</CardTitle>
              <CardDescription>
                Showing data from {formatDate(startDate)} to {formatDate(endDate)}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {selectedColumns.includes('name') && (
                        <TableHead>Name</TableHead>
                      )}
                      {selectedColumns.includes('status') && (
                        <TableHead>Status</TableHead>
                      )}
                      {selectedColumns.includes('createdAt') && (
                        <TableHead>Created Date</TableHead>
                      )}
                      {selectedColumns.includes('updatedAt') && (
                        <TableHead>Updated Date</TableHead>
                      )}
                      {selectedColumns.includes('owner') && (
                        <TableHead>Owner</TableHead>
                      )}
                      {selectedColumns.includes('type') && (
                        <TableHead>Type</TableHead>
                      )}
                      {selectedColumns.includes('organization') && (
                        <TableHead>Organization</TableHead>
                      )}
                      {selectedColumns.includes('project') && (
                        <TableHead>Project</TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previewData.map((item) => (
                      <TableRow key={item.id}>
                        {selectedColumns.includes('name') && (
                          <TableCell>{item.name}</TableCell>
                        )}
                        {selectedColumns.includes('status') && (
                          <TableCell>{item.status}</TableCell>
                        )}
                        {selectedColumns.includes('createdAt') && (
                          <TableCell>{format(new Date(item.createdAt), 'PP')}</TableCell>
                        )}
                        {selectedColumns.includes('updatedAt') && (
                          <TableCell>{format(new Date(item.updatedAt), 'PP')}</TableCell>
                        )}
                        {selectedColumns.includes('owner') && (
                          <TableCell>{item.owner}</TableCell>
                        )}
                        {selectedColumns.includes('type') && (
                          <TableCell>{item.type}</TableCell>
                        )}
                        {selectedColumns.includes('organization') && (
                          <TableCell>{item.organization}</TableCell>
                        )}
                        {selectedColumns.includes('project') && (
                          <TableCell>{item.project}</TableCell>
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

export default ReportsAnalyticsPage;