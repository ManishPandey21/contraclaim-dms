
import React from 'react';
import { Badge } from '@/components/ui/badge';
import { LetterStatus } from './types';

interface StatusBadgeProps {
  status: LetterStatus;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  switch (status) {
    case 'Input':
      return <Badge className="bg-gray-500">Input</Badge>;
    case 'Strategy':
      return <Badge className="bg-cyan-600">Strategy</Badge>;
    case 'Draft':
      return <Badge className="bg-blue-500">Draft</Badge>;
    case 'Review':
      return <Badge className="bg-yellow-500">Review</Badge>;
    case 'Approval':
      return <Badge className="bg-purple-500">Approval</Badge>;
    case 'Completed':
      return <Badge className="bg-green-500">Completed</Badge>;
    case 'Rejected':
      return <Badge className="bg-red-500">Rejected</Badge>;
    default:
      return <Badge>Unknown</Badge>;
  }
};
