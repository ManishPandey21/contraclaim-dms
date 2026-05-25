import { Badge } from "@/components/ui/badge";

interface GraphStatusBadgeProps {
  status?: string | null;
}

const STATUS_LABELS: Record<string, { label: string; variant: "default" | "secondary" | "outline" | "destructive" }> =
  {
    ready_for_review: { label: "Ready for Review", variant: "default" },
    needs_attention: { label: "Needs Attention", variant: "destructive" },
    success: { label: "Completed", variant: "default" },
    pending: { label: "Pending", variant: "secondary" },
    error: { label: "Error", variant: "destructive" },
    analysis_only: { label: "Strategy Plan", variant: "secondary" },
  };

export const GraphStatusBadge = ({ status }: GraphStatusBadgeProps) => {
  if (!status) {
    return <Badge variant="outline">No Drafting Run</Badge>;
  }
  const key = status.toLowerCase();
  const config = STATUS_LABELS[key] ?? {
    label: status,
    variant: "outline" as const,
  };
  return <Badge variant={config.variant}>{config.label}</Badge>;
};

export default GraphStatusBadge;
