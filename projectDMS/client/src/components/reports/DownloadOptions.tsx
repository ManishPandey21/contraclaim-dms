import React from 'react';
import { Button } from '@/components/ui/button';
import { Download } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface DownloadOptionsProps {
  onDownload: (format: 'csv' | 'pdf') => void;
  disabled: boolean;
  organization?: string;
  project?: string;
}

export const DownloadOptions: React.FC<DownloadOptionsProps> = ({
  onDownload,
  disabled,
  organization,
  project
}) => {
  // Create tooltip content based on filters
  const getTooltipContent = (format: string) => {
    let content = `Download ${format.toUpperCase()} report`;
    if (organization || project) {
      content += " for";
      if (organization) content += ` ${organization}`;
      if (organization && project) content += " -";
      if (project) content += ` ${project}`;
    }
    return content;
  };

  return (
    <div className="flex gap-2">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              onClick={() => onDownload('csv')}
              disabled={disabled}
            >
              <Download className="mr-2 h-4 w-4" />
              Download CSV
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>{getTooltipContent('csv')}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              onClick={() => onDownload('pdf')}
              disabled={disabled}
            >
              <Download className="mr-2 h-4 w-4" />
              Download PDF
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>{getTooltipContent('pdf')}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
};
