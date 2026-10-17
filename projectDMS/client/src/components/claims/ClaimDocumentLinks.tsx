import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import { CLAIM_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";

export default function ClaimDocumentLinks({
  claimId,
  organizationId,
  projectId,
  frozen = false,
  canManage = false,
}: {
  claimId: string;
  organizationId?: string | null;
  projectId?: string | null;
  frozen?: boolean;
  canManage?: boolean;
}) {
  return (
    <EntityDocumentLinks
      targetType="claim"
      targetId={claimId}
      organizationId={organizationId}
      projectId={projectId}
      roles={CLAIM_DOCUMENT_RELATIONSHIP_ROLES}
      defaultRole="supporting_document"
      frozen={frozen}
      canManage={canManage}
    />
  );
}
