
import React from 'react';
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue
} from "@/components/ui/select";
import { Search, Filter } from 'lucide-react';

interface StatusData {
  status: string;
  count: number;
  color: string;
}

interface DashboardHeaderProps {
  documentStatuses: StatusData[];
  statusFilter: string;
  setStatusFilter: (status: string) => void;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
}

const DashboardHeader: React.FC<DashboardHeaderProps> = ({
  documentStatuses,
  statusFilter,
  setStatusFilter,
  searchQuery,
  setSearchQuery
}) => {
  return (
    <div className="flex justify-between items-center mb-4">
      <h1 className="text-2xl font-bold">Document Dashboard</h1>

      <div className="flex items-center space-x-3">
        <div className="relative w-64">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search documents..."
            className="pl-8"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-40">
            <div className="flex items-center">
              <Filter className="mr-2 h-4 w-4" />
              <SelectValue placeholder="Filter by status" />
            </div>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Statuses</SelectItem>
            {documentStatuses.map((status) => (
              <SelectItem key={status.status} value={status.status}>
                {status.status}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
};

export default DashboardHeader;
