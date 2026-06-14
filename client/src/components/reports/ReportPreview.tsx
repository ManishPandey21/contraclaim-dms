import React from 'react';
import { format } from 'date-fns';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

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

interface ReportPreviewProps {
  data: ReportData[];
  selectedColumns: string[];
  startDate?: Date;
  endDate?: Date;
}

export const ReportPreview: React.FC<ReportPreviewProps> = ({
  data,
  selectedColumns,
  startDate,
  endDate
}) => {
  const formatDate = (dateString: string) => {
    return format(new Date(dateString), 'PP');
  };

  // If no data, don't render the preview
  if (data.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Report Preview</CardTitle>
        <CardDescription>
          {startDate && endDate 
            ? `Showing data from ${format(startDate, 'PP')} to ${format(endDate, 'PP')}`
            : 'Preview of generated report'
          }
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
              {data.map((item) => (
                <TableRow key={item.id}>
                  {selectedColumns.includes('name') && (
                    <TableCell>{item.name}</TableCell>
                  )}
                  {selectedColumns.includes('status') && (
                    <TableCell>{item.status}</TableCell>
                  )}
                  {selectedColumns.includes('createdAt') && (
                    <TableCell>{formatDate(item.createdAt)}</TableCell>
                  )}
                  {selectedColumns.includes('updatedAt') && (
                    <TableCell>{formatDate(item.updatedAt)}</TableCell>
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
  );
};
