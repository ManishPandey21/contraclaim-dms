import React, { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar as CalendarIcon, Chain, FileText, LinkIcon, Loader2, ExternalLink, Search, Copy, Link2, ChevronRight, Info, Tag, BookOpen } from "lucide-react";
import { format } from "date-fns";

interface RefParsed { raw: string; date?: string; letterNo?: string; linkedId?: string | null }
interface RefLinked { id: string; title?: string; letterNo?: string; date?: string }

interface Letter {
  id: string;
  letterNo?: string;
  date?: string;
  subject?: string;
  from_?: string;
  to?: string;
  summary?: string;
  keywords?: string[];
  clauses?: string[];
  referencesParsed?: RefParsed[];
  referencesLinked?: RefLinked[];
  thread?: {
    threadId: string;
    ancestors: RefLinked[];
    descendants: RefLinked[];
    lastInThread: boolean;
  };
}

const parseBulletString = (s?: string): string[] => {
  if (!s) return [];
  return s
    .split(/\r?\n/)
    .map((ln) => ln.trim().replace(/^[-*\d\.\)\s]+/, ""))
    .filter(Boolean);
};

const prettifyDate = (iso?: string) => {
  if (!iso) return "—";
  try { return format(new Date(iso), "MMM dd, yyyy"); } catch { return iso; }
};

const ReferencePage: React.FC = () => {
  const params = useParams<{ id?: string }>();
  const letterId = params.id;
  const [loading, setLoading] = useState(false);
  const [letter, setLetter] = useState<Letter | null>(null);
  const [query, setQuery] = useState("");
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [linking, setLinking] = useState(false);
  const [newRef, setNewRef] = useState("");

  useEffect(() => {
    const load = async () => {
      if (!letterId) return;
      setLoading(true);
      try {
        const mockRefs = [
          "AFC/PM/AGCC-02/03995 dated 13.10.2023",
          "Agra-LET-JVTI-TBM-00031-E01 dated 02.05.2023",
          "Agra-LET-JVTI-TBM-00017-E01 dated 09.03.2023",
          "Agra-LET-JVTI-TBM-00011-E01 dated 08.02.2023",
          "Agra-LET-JVTI-TBM-00008-E01 dated 18.01.2023",
          "Agra-LET-JVTI-CPM-00262-E01 dated 17.01.2023",
          "Agra-LET-JVTI-TBM-00003-E01 dated 05.12.2022",
          "Agra-LET-JVTI-TBM-00001-E01 dated 04.11.2022",
          "AFC/PM/AGCC-02/0548 dated 10.08.2022",
          "AGCC-02 Contract Agreement dated 22.04.2022",
          "LOA no. 379/UPMRC/CE-Contract/AGCC-02/2021-22 dated 14.03.2022"
        ];

        const parsedRefs: RefParsed[] = mockRefs.map((raw) => {
          const m = raw.match(/([A-Z0-9\-\/]+).*?dated\s+(\d{2}\.\d{2}\.\d{4}|\d{2}\.\d{2}\.\d{2,4}|\d{2}\.\d{2}\.\d{2})/i);
          return { raw, letterNo: m?.[1], date: m?.[2] };
        });

        const mock: Letter = {
          id: letterId,
          letterNo: "Agra-LET-JVTI-CPM-00346-E01",
          date: "2023-11-09T00:00:00.000Z",
          subject: "Regarding proposal for additional setup at Taj Mahal Shaft",
          from_: "TYPSA / ITALFERR - Consulting Engineers & Architects",
          to: "AFCONS INFRASTRUCTURE LIMITED",
          summary: [
            "Significant delays were observed in the procurement and delivery of TBMs.",
            "Delays of 91 and 123 days occurred in the commencement of TBMs 1 and 2.",
            "Key dates for TBMs 1 and 2 were missed, impacting the project's timeline.",
            "Contractor's claim for additional setup at Taj Mahal Shaft is not valid under the contract.",
            "Delays attributed to contractor's lack of planning and action."
          ].map((s) => `- ${s}`).join("\n"),
          keywords: ["TBMs", "Delay", "Contractual Key Dates", "Additional Setup", "Tunnelling Works"],
          clauses: ["AGCC-02 contract, GCC Sub Clause 8.6 (Rate of Progress)"],
          referencesParsed: parsedRefs,
          referencesLinked: [],
          thread: {
            threadId: "thread-123",
            ancestors: [],
            descendants: [],
            lastInThread: true,
          },
        };

        setLetter(mock);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [letterId]);

  const summaryLines = useMemo(() => parseBulletString(letter?.summary), [letter?.summary]);

  const filteredParsed = useMemo(() => {
    const base = letter?.referencesParsed || [];
    const q = query.trim().toLowerCase();
    if (!q) return base;
    return base.filter((r) => r.raw.toLowerCase().includes(q));
  }, [query, letter?.referencesParsed]);

  const copySummary = async () => {
    if (!summaryLines.length) return;
    await navigator.clipboard.writeText(summaryLines.map((s) => `• ${s}`).join("\n"));
  };

  const copyClauses = async () => {
    if (!letter?.clauses?.length) return;
    await navigator.clipboard.writeText(letter.clauses.join("\n"));
  };

  const openLinked = (id?: string | null) => {
    if (!id) return;
    window.open(`/documents/${id}`, "_blank");
  };

  const linkReference = async () => {
    if (!newRef.trim() || !letter) return;
    setLinking(true);
    try {
      const rp: RefParsed = { raw: newRef, letterNo: newRef, date: undefined, linkedId: Math.random().toString(36).slice(2) };
      setLetter({ ...letter, referencesParsed: [rp, ...(letter.referencesParsed || [])] });
      setNewRef("");
      setLinkDialogOpen(false);
    } catch (e) {
      console.error(e);
    } finally {
      setLinking(false);
    }
  };

  return (
    <div className="container mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Chain className="h-6 w-6" /> Letter References
          </h1>
          <p className="text-sm text-muted-foreground">View references, summary, keywords and clauses for the selected letter.</p>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input placeholder="Search references..." className="pl-8 w-[260px]" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>

          <Dialog open={linkDialogOpen} onOpenChange={setLinkDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="default">
                <Link2 className="mr-2 h-4 w-4" /> Attach / Link Reference
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Attach Reference</DialogTitle>
                <DialogDescription>Paste a Letter No. or reference string to link it to this letter.</DialogDescription>
              </DialogHeader>
              <div className="space-y-2">
                <Input placeholder="e.g. Agra-LET-JVTI-CPM-00262-E01" value={newRef} onChange={(e) => setNewRef(e.target.value)} />
                <p className="text-xs text-muted-foreground">We will search within the same organisation/project and link if found.</p>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setLinkDialogOpen(false)}>Cancel</Button>
                <Button onClick={linkReference} disabled={linking}>
                  {linking ? <Loader2 className="h-4 w-4 animate-spin" /> : <LinkIcon className="h-4 w-4 mr-2" />} Link
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <Tabs defaultValue="overview" className="w-full">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="references">References</TabsTrigger>
          <TabsTrigger value="summary">Summary & Keywords</TabsTrigger>
          <TabsTrigger value="clauses">Clauses</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2"><FileText className="h-4 w-4" /> Letter Overview</CardTitle>
                <CardDescription>Core metadata extracted from the letter</CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="text-muted-foreground text-sm">Loading...</div>
                ) : letter ? (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <div className="text-xs uppercase text-muted-foreground mb-1">Letter No.</div>
                      <div className="font-medium">{letter.letterNo || "—"}</div>
                    </div>
                    <div>
                      <div className="text-xs uppercase text-muted-foreground mb-1">Date</div>
                      <div className="font-medium flex items-center gap-2"><CalendarIcon className="h-4 w-4" /> {prettifyDate(letter.date)}</div>
                    </div>
                    <div className="md:col-span-2">
                      <div className="text-xs uppercase text-muted-foreground mb-1">Subject</div>
                      <div className="font-medium">{letter.subject || "—"}</div>
                    </div>
                    <div>
                      <div className="text-xs uppercase text-muted-foreground mb-1">From</div>
                      <div className="font-medium">{letter.from_ || "—"}</div>
                    </div>
                    <div>
                      <div className="text-xs uppercase text-muted-foreground mb-1">To</div>
                      <div className="font-medium">{letter.to || "—"}</div>
                    </div>
                  </div>
                ) : (
                  <div className="text-muted-foreground text-sm">No data</div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2"><Chain className="h-4 w-4" /> Conversation Chain</CardTitle>
                <CardDescription>Upstream/Downstream letters</CardDescription>
              </CardHeader>
              <CardContent>
                {letter?.thread ? (
                  <div className="space-y-3">
                    <div className="text-xs text-muted-foreground">Thread ID: {letter.thread.threadId}</div>
                    <div>
                      <div className="text-xs uppercase text-muted-foreground mb-1">Ancestors</div>
                      {letter.thread.ancestors.length ? (
                        <div className="space-y-1">
                          {letter.thread.ancestors.map((d) => (
                            <div key={d.id} className="text-sm flex items-center gap-2">
                              <ChevronRight className="h-3 w-3 text-muted-foreground" />
                              <button className="hover:underline" onClick={() => openLinked(d.id)}>
                                {d.letterNo || d.title || d.id}
                              </button>
                              <span className="text-muted-foreground">{prettifyDate(d.date)}</span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-sm text-muted-foreground">None</div>
                      )}
                    </div>
                    <div>
                      <div className="text-xs uppercase text-muted-foreground mb-1">Descendants</div>
                      {letter.thread.descendants.length ? (
                        <div className="space-y-1">
                          {letter.thread.descendants.map((d) => (
                            <div key={d.id} className="text-sm flex items-center gap-2">
                              <ChevronRight className="h-3 w-3 text-muted-foreground" />
                              <button className="hover:underline" onClick={() => openLinked(d.id)}>
                                {d.letterNo || d.title || d.id}
                              </button>
                              <span className="text-muted-foreground">{prettifyDate(d.date)}</span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-sm text-muted-foreground">None</div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">No chain data</div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="references">
          <Card>
            <CardHeader className="flex flex-col md:flex-row md:items-center md:justify-between gap-2">
              <div>
                <CardTitle className="text-lg flex items-center gap-2"><LinkIcon className="h-4 w-4" /> References (Ref.)</CardTitle>
                <CardDescription>All references mentioned in the letter and their link status</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" size="sm"><Info className="h-4 w-4 mr-2" /> How linking works</Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80 text-sm" align="end">
                    The system tries to match a reference by <b>Letter No.</b> within the same organisation/project. You can also attach a reference manually using the <i>Attach / Link Reference</i> button.
                  </PopoverContent>
                </Popover>
              </div>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Reference</TableHead>
                    <TableHead>Detected Date</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(filteredParsed || []).map((r, idx) => (
                    <TableRow key={idx}>
                      <TableCell className="font-medium">{r.raw}</TableCell>
                      <TableCell>{r.date || "—"}</TableCell>
                      <TableCell>
                        {r.linkedId ? (
                          <Badge className="bg-green-500">Linked</Badge>
                        ) : (
                          <Badge className="bg-amber-500">Unlinked</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          {r.linkedId ? (
                            <Button variant="ghost" size="icon" onClick={() => openLinked(r.linkedId)}>
                              <ExternalLink className="h-4 w-4" />
                            </Button>
                          ) : (
                            <Button variant="ghost" size="sm" onClick={() => { setNewRef(r.letterNo || r.raw); setLinkDialogOpen(true); }}>
                              <Link2 className="h-4 w-4 mr-1" /> Link
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="summary">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2"><FileText className="h-4 w-4" /> Summary</CardTitle>
                <CardDescription>Concise points addressed in the letter</CardDescription>
              </CardHeader>
              <CardContent>
                {summaryLines.length ? (
                  <>
                    <ul className="list-disc pl-5 space-y-1">
                      {summaryLines.map((s, i) => (<li key={i} className="text-sm">{s}</li>))}
                    </ul>
                    <div className="mt-3">
                      <Button size="sm" variant="outline" onClick={copySummary}><Copy className="h-4 w-4 mr-2" /> Copy summary</Button>
                    </div>
                  </>
                ) : (
                  <div className="text-sm text-muted-foreground">No summary available.</div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2"><Tag className="h-4 w-4" /> Keywords</CardTitle>
                <CardDescription>Key contractual/technical terms</CardDescription>
              </CardHeader>
              <CardContent>
                {letter?.keywords?.length ? (
                  <div className="flex flex-wrap gap-2">
                    {letter.keywords.map((k, i) => (
                      <Badge key={i} variant="secondary" className="text-sm py-1 px-2">{k}</Badge>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">No keywords detected.</div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="clauses">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2"><BookOpen className="h-4 w-4" /> Contractual Clauses</CardTitle>
              <CardDescription>Clauses explicitly mentioned or inferred</CardDescription>
            </CardHeader>
            <CardContent>
              {letter?.clauses?.length ? (
                <>
                  <ul className="list-disc pl-5 space-y-1">
                    {letter.clauses.map((c, i) => (<li key={i} className="text-sm">{c}</li>))}
                  </ul>
                  <div className="mt-3">
                    <Button size="sm" variant="outline" onClick={copyClauses}><Copy className="h-4 w-4 mr-2" /> Copy clauses</Button>
                  </div>
                </>
              ) : (
                <div className="text-sm text-muted-foreground">No clauses captured.</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Separator className="my-6" />

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Notes</CardTitle>
          <CardDescription>Use this section to record manual observations</CardDescription>
        </CardHeader>
        <CardContent>
          <Textarea placeholder="Add notes..." rows={4} />
          <div className="mt-3 flex justify-end">
            <Button variant="outline">Save Note</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default ReferencePage;
