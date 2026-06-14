import React from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Clock, Tag, MessageSquare } from "lucide-react";
import { formatDateTime, timeAgo } from "@/utils/datetime";

interface ActivityItem {
  id: string;
  action: string;
  timestamp: string;
  user?: string;
}

interface DocumentDetailsPanelProps {
  document: {
    createdAt: string;
    modifiedAt: string;
    createdBy: string;
    version: string;
    tags: string[];
  };
  activity?: ActivityItem[];
}

const DocumentDetailsPanel: React.FC<DocumentDetailsPanelProps> = ({
  document,
  activity,
}) => {
  const renderDateTime = (iso: string) => formatDateTime(iso);

  return (
    <div className="space-y-5">
      <Card>
        <CardContent className="p-4 space-y-4">
          <div>
            <h3 className="font-medium text-sm flex items-center gap-2">
              <Clock className="h-4 w-4 text-muted-foreground" />
              Document History
            </h3>
            <div className="mt-2 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Created</span>
                <span>{renderDateTime(document.createdAt)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Modified</span>
                <span>{renderDateTime(document.modifiedAt)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Created By</span>
                <span>{document.createdBy}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Version</span>
                <span>{document.version}</span>
              </div>
            </div>
          </div>

          <div className="pt-2">
            <h3 className="font-medium text-sm flex items-center gap-2">
              <Tag className="h-4 w-4 text-muted-foreground" />
              Tags
            </h3>
            <div className="flex flex-wrap gap-2 mt-2">
              {document.tags.map((tag) => (
                <Badge key={tag} variant="neutral" className="text-xs">
                  {tag}
                </Badge>
              ))}
              <Button
                variant="outline"
                size="sm"
                className="h-6 text-xs rounded-full"
              >
                + Add Tag
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-4">
          <h3 className="font-medium text-sm flex items-center gap-2 mb-2">
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
            Activity
          </h3>
          <div className="space-y-3 text-sm">
            {activity && activity.length > 0 ? (
              activity.map((item) => (
                <div
                  key={item.id}
                  className="border-l-2 border-muted pl-3 py-1"
                >
                  <p className="font-medium">
                    {item.user ? `${item.user} ${item.action}` : item.action}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    {timeAgo(item.timestamp)}
                  </p>
                </div>
              ))
            ) : (
              <div className="text-muted-foreground text-sm">
                No recent activity.
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default DocumentDetailsPanel;
