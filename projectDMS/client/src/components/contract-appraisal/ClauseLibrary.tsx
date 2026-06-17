import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { BookText, Loader2, Search } from "lucide-react";
import { ClauseEntry, listClauses } from "@/services/contracts-api";

interface Props {
  organizationId?: string;
  projectId?: string;
}

const ClauseLibrary: React.FC<Props> = ({ organizationId, projectId }) => {
  const [q, setQ] = useState("");
  const [clauses, setClauses] = useState<ClauseEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  const search = async () => {
    if (!projectId?.trim()) return;
    setLoading(true);
    try {
      setClauses(
        await listClauses({
          organization_id: organizationId || undefined,
          project_id: projectId,
          q: q.trim() || undefined,
          limit: 100,
        }),
      );
      setSearched(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookText className="h-5 w-5" />
          Clause Library
        </CardTitle>
        <CardDescription>
          Read-only index of indexed contract clauses for this project.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by clause number, title or text"
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <Button onClick={search} disabled={loading || !projectId?.trim()}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Search className="mr-2 h-4 w-4" />}
            Search
          </Button>
        </div>

        {searched && clauses.length === 0 && (
          <p className="text-sm text-muted-foreground">No indexed clauses found for this project.</p>
        )}

        <div className="space-y-2">
          {clauses.map((c, i) => (
            <div key={`${c.document_id}-${c.clause_number}-${i}`} className="rounded-md border p-2">
              <div className="flex items-center gap-2">
                {c.clause_number && <Badge variant="outline">Clause {c.clause_number}</Badge>}
                <span className="text-sm font-medium">{c.clause_title || c.document_name || "Clause"}</span>
                {c.page_numbers.length > 0 && (
                  <span className="text-xs text-muted-foreground">p.{c.page_numbers.join(", ")}</span>
                )}
              </div>
              {c.snippet && <p className="mt-1 text-xs text-muted-foreground">{c.snippet}</p>}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
};

export default ClauseLibrary;
