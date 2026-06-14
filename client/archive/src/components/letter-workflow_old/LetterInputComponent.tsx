import React, { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle, Send, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { PendencyIndicator } from "./PendencyIndicator";
import { format } from "date-fns";
import { api } from "@/services/api";

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface InputRequest {
  id: string;
  requestedBy: User;
  requestDetails: string;
  dueDate?: string;
  createdAt: string;
  response?: string;
  respondedAt?: string;
  status?: string;
}

interface Letter {
  id: string;
  title: string;
  recipient: string;
  subject: string;
  content: string;
  status: string;
  createdBy: User;
  assignedTo: User;
  createdAt: string;
  updatedAt: string;
  statusStartDate?: string;
  comments?: string[];
  inputRequests?: InputRequest[];
  // Optional linkage to letter/document number (via selected reference)
  reference?: {
    referenceNumber?: string;
    reference_number?: string;
  };
  // project/org context for headers
  projectId?: string;
  project_id?: string;
  organizationId?: string;
  organization_id?: string;
}

interface LetterInputComponentProps {
  letter: Letter;
  onInput: (updatedLetter: Letter) => void;
  onCancel: () => void;
}

const LetterInputComponent: React.FC<LetterInputComponentProps> = ({
  letter,
  onInput,
  onCancel,
}) => {
  const [inputResponse, setInputResponse] = useState("");

  // Load users for mapping requested_from -> User
  const [users, setUsers] = useState<User[]>([]);
  const [requests, setRequests] = useState<InputRequest[]>([]);
  const [loadingReqs, setLoadingReqs] = useState(false);

  // Prefer explicit letter_no if present (set during initiation), then reference numbers, else fallback
  const letterNumber =
    (letter as any).letter_no ??
    (letter as any).reference?.referenceNumber ??
    (letter as any).reference?.reference_number ??
    "New Draft";

  useEffect(() => {
    // preload users used to resolve requested_from -> name
    const loadUsers = async () => {
      try {
        const { data } = await api.get<any[]>("/users");
        const mapped =
          (Array.isArray(data) ? data : []).map((u: any) => ({
            id: u.id ?? u._id ?? String(u.email ?? ""),
            name: u.username ?? u.name ?? u.full_name ?? u.email ?? "User",
            email: u.email ?? "",
            avatar: u.avatar ?? undefined,
          })) ?? [];
        setUsers(mapped);
      } catch {
        setUsers([]);
      }
    };
    loadUsers();
  }, []);

  // Load requests for this letter and keep it reusable (for event-driven refresh)
  const loadRequests = useCallback(async () => {
    if (!(letter as any).id) return;
    setLoadingReqs(true);
    try {
      const { data } = await api.get<any[]>(
        `/input-requests/letter/${(letter as any).id}`,
        {
          headers: {
            "X-Org-Id":
              (letter as any).organizationId ??
              (letter as any).organization_id ??
              "",
            "X-Proj-Id":
              (letter as any).projectId ?? (letter as any).project_id ?? "",
          },
        }
      );
      const byId = new Map(users.map((u) => [u.id, u]));
      const mapped: InputRequest[] =
        (Array.isArray(data) ? data : []).map((r: any) => {
          const reqUser =
            byId.get(String(r.requested_from)) ??
            ({
              id: String(r.requested_from ?? ""),
              name: String(r.requested_from ?? "User"),
              email: "",
            } as User);
          return {
            id: String(r.id ?? r._id ?? ""),
            requestedBy: reqUser,
            requestDetails: String(r.details ?? ""),
            dueDate: r.due_date ?? undefined,
            createdAt: r.created_at ?? new Date().toISOString(),
            response: r.response ?? r.message ?? undefined,
            respondedAt: r.responded_at ?? undefined,
            status: r.status ?? undefined,
          };
        }) ?? [];
      setRequests(mapped);
    } catch (e: any) {
      toast.error("Failed to load input requests", {
        description: e?.response?.data?.detail || e?.message || "Unknown error",
      });
      setRequests([]);
    } finally {
      setLoadingReqs(false);
    }
  }, [letter, users]);

  // Initial and dependency-based load
  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  // Listen for newly created input requests to refresh the lists in this tab
  useEffect(() => {
    const handler = (e: any) => {
      const lid = e?.detail?.letterId;
      if (!lid) return;
      if (String(lid) === String((letter as any).id)) {
        loadRequests();
      }
    };
    window.addEventListener("input-request:created", handler as EventListener);
    return () => {
      window.removeEventListener(
        "input-request:created",
        handler as EventListener
      );
    };
  }, [loadRequests, letter]);

  const handleProvideInput = async () => {
    if (!inputResponse.trim()) {
      toast.error("Please provide your input response");
      return;
    }

    try {
      // respond to all pending requests
      const pending = requests.filter((r) => !r.response);
      for (const r of pending) {
        await api.post(`/input-requests/${r.id}/respond`, {
          message: inputResponse,
        });
      }
      toast.success("Input response submitted");

      // If all pending addressed, move to Draft locally
      const now = new Date().toISOString();
      const updatedRequests = requests.map((r) =>
        r.response
          ? r
          : {
              ...r,
              response: inputResponse,
              respondedAt: now,
            }
      );

      const updatedLetter: Letter = {
        ...letter,
        status: "Draft",
        updatedAt: now,
        statusStartDate: now,
        inputRequests: updatedRequests,
        content: inputResponse || letter.content,
      };

      onInput(updatedLetter);
    } catch (e: any) {
      toast.error("Failed to submit input response", {
        description: e?.response?.data?.detail || e?.message || "Unknown error",
      });
    }
  };

  const pendingRequests = requests.filter((req) => !req.response);
  const completedRequests = requests.filter((req) => req.response);

  return (
    <div className="space-y-4">
      <div className="rounded-md border p-4">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <Label className="text-muted-foreground text-sm">
              Letter Title
            </Label>
            <div className="font-medium">{letter.title}</div>
          </div>
          <div>
            <Label className="text-muted-foreground text-sm">Recipient</Label>
            <div className="font-medium">{letter.recipient}</div>
          </div>
        </div>

        <div className="mb-2">
          <Label className="text-muted-foreground text-sm">Subject</Label>
          <div className="font-medium">{letter.subject}</div>
        </div>

        <div className="mb-4">
          <Label className="text-muted-foreground text-sm">Letter Number</Label>
          <div className="font-medium">{letterNumber}</div>
        </div>

        <PendencyIndicator
          status={letter.status}
          date={letter.statusStartDate || letter.createdAt}
        />
      </div>

      {/* Pending Input Requests */}
      {pendingRequests.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <Send className="h-5 w-5" />
            Input Requests
          </h3>
          {pendingRequests.map((request) => (
            <Card key={request.id} className="border-amber-200 bg-amber-50">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-medium">
                    Input requested by {request.requestedBy.name}
                  </CardTitle>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant="warning"
                      className="bg-amber-100 text-amber-800"
                    >
                      Pending
                    </Badge>
                    {request.dueDate && (
                      <span className="text-xs text-muted-foreground">
                        Due: {format(new Date(request.dueDate), "MMM d, yyyy")}
                      </span>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="mb-3">
                  <Label className="text-sm font-medium">
                    Request Details:
                  </Label>
                  <p className="text-sm text-muted-foreground mt-1">
                    {request.requestDetails}
                  </p>
                </div>

                <div className="space-y-3">
                  <div>
                    <Label htmlFor="input-response">Your Response</Label>
                    <Textarea
                      id="input-response"
                      value={inputResponse}
                      onChange={(e) => setInputResponse(e.target.value)}
                      rows={6}
                      placeholder="Provide the requested information or clarification..."
                      className="resize-none"
                    />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Completed Input Requests */}
      {completedRequests.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <CheckCircle className="h-5 w-5 text-green-600" />
            Completed Inputs
          </h3>
          {completedRequests.map((request) => (
            <Card key={request.id} className="border-green-200 bg-green-50">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-medium">
                    Input requested by {request.requestedBy.name}
                  </CardTitle>
                  <Badge
                    variant="success"
                    className="bg-green-100 text-green-800"
                  >
                    Completed
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="mb-3">
                  <Label className="text-sm font-medium">Request:</Label>
                  <p className="text-sm text-muted-foreground mt-1">
                    {request.requestDetails}
                  </p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Your Response:</Label>
                  <p className="text-sm mt-1">{request.response}</p>
                  {request.respondedAt && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Responded on{" "}
                      {format(
                        new Date(request.respondedAt),
                        "MMM d, yyyy h:mm a"
                      )}
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        {pendingRequests.length > 0 && (
          <Button onClick={handleProvideInput} className="gap-2">
            <ArrowRight className="h-4 w-4" />
            Provide Input & Continue to Draft
          </Button>
        )}
      </div>
    </div>
  );
};

export default LetterInputComponent;
