import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Link2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

import {
  listDocumentEntityLinks,
  type DocumentRelationship,
} from "@/services/document-relationships-api";

/** Readable names for relationship target types; unknown types show raw. */
const TARGET_TYPE_LABELS: Record<string, string> = {
  delay_event: "Hindrance / constraint",
  claim: "Claim",
  ipc_bill: "IPC / bill",
  insurance: "Insurance",
  bank_guarantee_event: "Bank guarantee",
  key_date_achievement: "Key date achievement",
  eot_submission: "EOT submission",
  eot_determination: "EOT determination",
  contract_document: "Contract document",
};

export default function LinkedRecordsPanel({ documentId }: { documentId: string }) {
  const [links, setLinks] = useState<DocumentRelationship[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    listDocumentEntityLinks(documentId)
      .then((items) => {
        if (active) setLinks(items);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [documentId, reload]);

  if (loading) {
    return (
      <p className="flex items-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading linked records…
      </p>
    );
  }

  if (error) {
    return (
      <div role="alert" className="space-y-2 text-sm text-destructive">
        <p className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4" /> Linked records could not be loaded.
        </p>
        <Button type="button" variant="outline" size="sm" onClick={() => setReload((value) => value + 1)}>
          Retry
        </Button>
      </div>
    );
  }

  if (links.length === 0) {
    return <p className="text-sm text-muted-foreground">No linked records.</p>;
  }

  return (
    <div className="space-y-2">
      {links.map((link) => (
        <Link
          key={link._id}
          to={link.target_route || `/${link.target_type}/${link.target_id}`}
          className="flex items-start gap-2 rounded-md border p-2 text-sm hover:bg-muted/40"
        >
          <Link2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <span className="block font-medium">
              {link.target_label || link.target_id || "Linked record"}
            </span>
            <span className="text-xs text-muted-foreground">
              {TARGET_TYPE_LABELS[link.target_type || ""] || link.target_type} ·{" "}
              {link.relationship_role.replace(/_/g, " ")}
            </span>
          </span>
        </Link>
      ))}
    </div>
  );
}
