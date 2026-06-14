import React from "react";
import { Card, CardContent } from "@/components/ui/card";
import {
  FileText,
  MessageCircleQuestion,
  AlertCircle,
  Clock,
} from "lucide-react";

interface StatusCardProps {
  title: string;
  count: number;
  icon: React.ReactNode;
}

const StatusCard = ({ title, count, icon }: StatusCardProps) => (
  <Card className="glass-card">
    <CardContent className="p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-500">{title}</p>
          <h3 className="text-2xl font-semibold mt-1">{count}</h3>
        </div>
        {icon}
      </div>
    </CardContent>
  </Card>
);

interface StatusSummaryProps {
  totalDocumentsCount: number;
  incomingCount: number;
  outgoingCount: number;
  draftInProcessCount: number;
}

const StatusSummary: React.FC<StatusSummaryProps> = ({
  totalDocumentsCount,
  incomingCount,
  outgoingCount,
  draftInProcessCount,
}) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
      <StatusCard
        title="Total Documents"
        count={totalDocumentsCount}
        icon={
          <div className="p-2 rounded-full bg-blue-100">
            <FileText className="text-blue-600" size={24} />
          </div>
        }
      />

      <StatusCard
        title="Incoming"
        count={incomingCount}
        icon={
          <div className="p-2 rounded-full bg-amber-100">
            <MessageCircleQuestion className="text-amber-600" size={24} />
          </div>
        }
      />

      <StatusCard
        title="Outgoing"
        count={outgoingCount}
        icon={
          <div className="p-2 rounded-full bg-red-100">
            <AlertCircle className="text-red-600" size={24} />
          </div>
        }
      />

      <StatusCard
        title="Draft in Process"
        count={draftInProcessCount}
        icon={
          <div className="p-2 rounded-full bg-purple-100">
            <Clock className="text-purple-600" size={24} />
          </div>
        }
      />
    </div>
  );
};

export default StatusSummary;
