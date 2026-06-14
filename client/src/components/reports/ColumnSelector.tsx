import React from 'react';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';

interface Column {
  id: string;
  label: string;
}

interface ColumnSelectorProps {
  availableColumns: Column[];
  selectedColumns: string[];
  onColumnToggle: (columnId: string) => void;
}

export const ColumnSelector: React.FC<ColumnSelectorProps> = ({
  availableColumns,
  selectedColumns,
  onColumnToggle
}) => {
  return (
    <div className="space-y-2">
      <Label>Select Columns to Include</Label>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
        {availableColumns.map(column => (
          <div key={column.id} className="flex items-center space-x-2">
            <Checkbox 
              id={`column-${column.id}`} 
              checked={selectedColumns.includes(column.id)}
              onCheckedChange={() => onColumnToggle(column.id)}
            />
            <label 
              htmlFor={`column-${column.id}`}
              className="text-sm cursor-pointer"
            >
              {column.label}
            </label>
          </div>
        ))}
      </div>
    </div>
  );
};