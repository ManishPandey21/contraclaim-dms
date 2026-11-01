
import React from 'react';
import { Clock, AlertTriangle, History } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface PendencyIndicatorProps {
  date: string;
  status: string;
}

export const PendencyIndicator: React.FC<PendencyIndicatorProps> = ({
  date,
  status
}) => {
  // Calculate days in current status
  const calculateDaysInStatus = () => {
    const statusDate = new Date(date);
    const currentDate = new Date();

    // Calculate difference in milliseconds
    const diffTime = Math.abs(currentDate.getTime() - statusDate.getTime());

    // Convert to days
    const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24));

    return diffDays;
  };

  const daysInStatus = calculateDaysInStatus();

  // Determine urgency levels based on status and days
  const getUrgencyLevel = () => {
    // Completed and Rejected statuses don't need urgency levels
    if (status === 'Completed' || status === 'Rejected') {
      return 'normal';
    }

    // Different statuses might have different urgency thresholds
    switch (status) {
      case 'Input':
      case 'Draft':
        return daysInStatus > 7 ? 'high' : daysInStatus > 3 ? 'medium' : 'normal';
      case 'Review':
        return daysInStatus > 5 ? 'high' : daysInStatus > 2 ? 'medium' : 'normal';
      case 'Approval':
        return daysInStatus > 3 ? 'high' : daysInStatus > 1 ? 'medium' : 'normal';
      default:
        return 'normal';
    }
  };

  const urgency = getUrgencyLevel();

  // Get appropriate styling based on urgency
  const getUrgencyStyles = () => {
    switch (urgency) {
      case 'high':
        return {
          icon: <AlertTriangle className="h-4 w-4 text-red-500" />,
          textColor: 'text-red-500',
          label: 'High Urgency'
        };
      case 'medium':
        return {
          icon: <Clock className="h-4 w-4 text-amber-500" />,
          textColor: 'text-amber-500',
          label: 'Medium Urgency'
        };
      default:
        return {
          icon: <History className="h-4 w-4 text-gray-500" />,
          textColor: 'text-gray-500',
          label: 'Normal'
        };
    }
  };

  const { icon, textColor, label } = getUrgencyStyles();

  // Don't show pendency for completed or rejected letters
  if (status === 'Completed' || status === 'Rejected') {
    return <span className="text-gray-500">-</span>;
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className={`flex items-center gap-1 ${textColor}`}>
            {icon}
            <span>{daysInStatus} {daysInStatus === 1 ? 'day' : 'days'}</span>
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <p>{label}: {daysInStatus} {daysInStatus === 1 ? 'day' : 'days'} in {status} status</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};
