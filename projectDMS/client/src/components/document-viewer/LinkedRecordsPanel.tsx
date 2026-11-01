import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Link2, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";

import LinkToRecordDialog from "@/components/document-viewer/LinkToRecordDialog";
import { LINK_TO_RECORD_TARGETS, relationshipRoleLabel, targetTypeLabel } from "@/lib/relationship-roles";
import {
  listDocumentEntityLinks,
  listDocumentLinkTargetTypes,
  type DocumentRelationship,
} from "@/services/document-relationships-api";

export default function LinkedRecordsPanel({ documentId }: { documentId: string }) {
  const [links, setLinks] = useState<DocumentRelationship[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [linking, setLinking] = useState(false);
  // Register types this caller could link to, from their own permissions in the
  // current selection (no record is read to decide). `null` while unknown.
  const [linkableTypes, setLinkableTypes] = useState<string[] | null>(null);

  useEffect(() => {
    let active = true;
    setLinkableTypes(null);
    listDocumentLinkTargetTypes(documentId)
      .then((types) => {
        // Only registers this client can present; an unknown type never opens an empty dialog.
        if (active) setLinkableTypes(types.filter((type) => LINK_TO_RECORD_TARGETS.some((item) => item.value === type)));
      })
      .catch(() => {
        // Fail closed: without an answer the action is not offered.
        if (active) setLinkableTypes([]);
      });
    return () => {
      active = false;
    };
  }, [documentId]);

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

  const linkedKeys = useMemo(
    () => new Set(links.map((link) => `${link.target_type}:${link.target_id}:${link.relationship_role}`)),
    [links],
  );

  let body: JSX.Element;
  if (loading) {
    body = (
      <p className="flex items-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading linked records…
      </p>
    );
  } else if (error) {
    body = (
      <div role="alert" className="space-y-2 text-sm text-destructive">
        <p className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4" /> Linked records could not be loaded.
        </p>
        <Button type="button" variant="outline" size="sm" onClick={() => setReload((value) => value + 1)}>
          Retry
        </Button>
      </div>
    );
  } else if (links.length === 0) {
    body = <p className="text-sm text-muted-foreground">No linked records.</p>;
  } else {
    body = (
      <div className="space-y-2">
        {links.map((link) => (
          <Link
            key={link._id}
            data-testid="linked-record"
            to={link.target_route || `/${link.target_type}/${link.target_id}`}
            className="flex items-start gap-2 rounded-md border p-2 text-sm hover:bg-muted/40"
          >
            <Link2 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <span className="block font-medium">
                {link.target_label || link.target_id || "Linked record"}
              </span>
              <span className="text-xs text-muted-foreground">
                {targetTypeLabel(link.target_type)} · {relationshipRoleLabel(link.relationship_role)}
              </span>
            </span>
          </Link>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {linkableTypes && linkableTypes.length > 0 && (
        <div className="flex items-center justify-end">
          <Button type="button" variant="outline" size="sm" onClick={() => setLinking(true)}>
            <Plus className="mr-1 h-4 w-4" /> Link to Record
          </Button>
        </div>
      )}
      {body}
      {linkableTypes && linkableTypes.length > 0 && (
        <LinkToRecordDialog
          documentId={documentId}
          open={linking}
          onOpenChange={setLinking}
          linkedKeys={linkedKeys}
          targetTypes={linkableTypes}
          onLinked={() => setReload((value) => value + 1)}
        />
      )}
    </div>
  );
}
