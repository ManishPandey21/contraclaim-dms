import React from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { BarChart3 } from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Legend,
} from "recharts";

interface StatusData {
  status: string;
  count: number;
  color: string;
}

interface RecentDocument {
  id: string;
  documentNumber: string;
  filename: string;
  subject: string;
  direction: string;
  letterDate: string;
  uploadedAt: string;
}

interface StatusBreakdownProps {
  documentStatuses: StatusData[];
  recentDocuments: RecentDocument[];
  formatDate: (dateString: string) => string;
}

const StatusBreakdown: React.FC<StatusBreakdownProps> = ({
  documentStatuses,
  recentDocuments,
  formatDate,
}) => {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <Card className="glass-card lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-lg font-semibold flex items-center gap-2">
            <BarChart3 size={20} className="text-docsumo-blue" />
            Document Status Breakdown
          </CardTitle>
          <CardDescription>
            Distribution of documents by current status
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={documentStatuses}
                margin={{
                  top: 20,
                  right: 30,
                  left: 20,
                  bottom: 60,
                }}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="status"
                  angle={-45}
                  textAnchor="end"
                  height={70}
                  tick={{ fontSize: 12 }}
                />
                <YAxis />
                <Tooltip
                  formatter={(value) => [`${value} Documents`, "Count"]}
                  contentStyle={{
                    backgroundColor: "white",
                    border: "1px solid #f0f0f0",
                    borderRadius: "8px",
                    boxShadow: "0 4px 12px rgba(0, 0, 0, 0.1)",
                  }}
                />
                <Legend />
                <Bar dataKey="count" name="Documents" fill="#3B82F6">
                  {documentStatuses.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card className="glass-card">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">
            Recent Activity
          </CardTitle>
          <CardDescription>Newest uploaded documents</CardDescription>
        </CardHeader>
        <CardContent>
          {recentDocuments.length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-4">
              No recent documents
            </p>
          ) : (
            <ul className="space-y-4">
              {recentDocuments.map((document, index) => {
                const primaryLabel =
                  document.documentNumber ||
                  document.filename ||
                  document.subject ||
                  "Untitled";
                const metadata = [
                  document.direction,
                  document.letterDate
                    ? `Letter Date ${formatDate(document.letterDate)}`
                    : "",
                  document.uploadedAt
                    ? `Uploaded ${formatDate(document.uploadedAt)}`
                    : "",
                ].filter(Boolean);

                return (
                  <li
                    key={`${document.id}-${index}`}
                    className="flex items-start gap-3 pb-3 border-b border-gray-100 last:border-0"
                  >
                    <div className="mt-1 w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p
                        className="text-sm font-medium truncate"
                        title={primaryLabel}
                      >
                        {primaryLabel}
                      </p>
                      <p
                        className="text-xs text-gray-500 mt-0.5 truncate"
                        title={document.subject}
                      >
                        {document.subject || "No subject available"}
                      </p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {metadata.join(" • ")}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default StatusBreakdown;
