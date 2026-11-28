import React, { useEffect, useState } from "react";
import { FileText, Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { searchDocuments, type SearchResult } from "@/services/search-api";

interface ReplyToLetterDialogProps {
  onPick: (documentId: string) => void;
}

/** "Reply to a letter": pick the incoming letter. That is the whole form. */
export const ReplyToLetterDialog: React.FC<ReplyToLetterDialogProps> = ({ onPick }) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");

  useEffect(() => {
    if (!open) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      setState("loading");
      try {
        const response = await searchDocuments({
          query,
          limit: 12,
          dateRange: {},
          fileTypes: [],
          organizations: [],
          projects: [],
          categories: [],
          sortBy: "date",
          sortOrder: "desc",
        } as unknown as Parameters<typeof searchDocuments>[0]);
        if (active) {
          setResults(response.results ?? []);
          setState("idle");
        }
      } catch {
        if (active) setState("error");
      }
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <FileText className="mr-2 h-4 w-4" aria-hidden="true" />
          Reply to a letter
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Which letter are you replying to?</DialogTitle>
          <DialogDescription>
            Pick the incoming letter. It will be read and broken into its assertions; there is nothing else to
            fill in.
          </DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <label htmlFor="reply-letter-search" className="sr-only">
            Search letters
          </label>
          <Input
            id="reply-letter-search"
            autoFocus
            value={query}
            placeholder="Search by letter number, subject or sender"
            onChange={(event) => setQuery(event.target.value)}
            className="pl-9"
          />
        </div>
        <div aria-live="polite" className="max-h-80 min-h-24 overflow-y-auto">
          {state === "loading" && (
            <p className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> Searching…
            </p>
          )}
          {state === "error" && (
            <p className="p-3 text-sm text-destructive">Letters could not be searched. Try again.</p>
          )}
          {state === "idle" && results.length === 0 && (
            <p className="p-3 text-sm text-muted-foreground">No letters match.</p>
          )}
          <ul className="divide-y">
            {results.map((result) => (
              <li key={result._id}>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onPick(result._id);
                  }}
                  className="flex w-full flex-col items-start gap-0.5 px-3 py-2.5 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                >
                  <span className="text-sm font-medium text-foreground">{result.name || result.filename}</span>
                  {result.excerpt && (
                    <span className="line-clamp-1 text-xs text-muted-foreground">{result.excerpt}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ReplyToLetterDialog;
