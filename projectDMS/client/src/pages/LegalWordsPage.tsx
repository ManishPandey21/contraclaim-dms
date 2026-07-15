import React, { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  BookOpen,
  CalendarDays,
  Loader2,
  Search,
  Send,
  Tag,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  LegalWord,
  LegalWordPublishedItem,
  getPublishedLegalWords,
  searchLegalWord,
} from "@/services/legal-words-api";

const PAGE_SIZE = 6;

const formatDate = (value?: string | null) => {
  if (!value) return "";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
};

const LegalWordsPage = () => {
  const [words, setWords] = useState<LegalWordPublishedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [searchMessage, setSearchMessage] = useState("");
  const [searchResult, setSearchResult] = useState<LegalWord | null>(null);

  const loadPublished = useCallback(async (skip = 0) => {
    const isInitial = skip === 0;
    if (isInitial) setLoading(true);
    else setLoadingMore(true);
    try {
      const response = await getPublishedLegalWords({
        skip,
        limit: PAGE_SIZE,
      });
      setWords((current) => (isInitial ? response.words : [...current, ...response.words]));
      setTotal(response.total);
      setHasNext(Boolean(response.has_next));
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Failed to load legal words");
    } finally {
      if (isInitial) setLoading(false);
      else setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void loadPublished(0);
  }, [loadPublished]);

  const onSearch = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    setSearching(true);
    setSearchResult(null);
    setSearchMessage("");
    try {
      const response = await searchLegalWord(trimmed);
      setSearchResult(response.word || null);
      setSearchMessage(response.message || (response.found ? "" : "This word is not available in the database yet."));
      if (response.requested) toast.success("Submitted for admin review");
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Search failed");
    } finally {
      setSearching(false);
    }
  };

  const hasPublishedWords = words.length > 0;
  const statusLabel = useMemo(
    () => `${total} published word${total === 1 ? "" : "s"}`,
    [total],
  );

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 p-4 md:p-6">
      <header className="flex flex-col gap-4 border-b border-gray-200 pb-5 md:flex-row md:items-end md:justify-between">
        <div className="flex items-start gap-3">
          <div className="mt-1 rounded-md bg-blue-50 p-2 text-blue-700">
            <BookOpen className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-gray-950">Learn Contractual/Legal Words</h1>
            <p className="mt-1 text-sm text-gray-600">
              Published legal and contractual words, newest first.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex w-fit items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700">
            <CalendarDays className="h-4 w-4 text-blue-700" />
            {statusLabel}
          </div>
          <Button variant="outline" onClick={() => void loadPublished(0)} disabled={loading}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Refresh
          </Button>
        </div>
      </header>

      <section className="space-y-4">
        {loading ? (
          <div className="flex min-h-48 items-center justify-center rounded-md border border-dashed border-gray-300 bg-white text-sm text-gray-600">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading published words
          </div>
        ) : hasPublishedWords ? (
          <>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {words.map((word, index) => (
                <WordCard
                  key={`${word.id || word.word}-${word.display_publication_date}-${index}`}
                  word={word}
                  index={index + 1}
                />
              ))}
            </div>
            {hasNext ? (
              <div className="flex justify-center">
                <Button
                  variant="outline"
                  onClick={() => void loadPublished(words.length)}
                  disabled={loadingMore}
                >
                  {loadingMore ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Load More
                </Button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            No legal or contractual words have been published yet.
          </div>
        )}
      </section>

      <section className="border-t border-gray-200 pt-5">
        <div className="mb-3 flex items-center gap-2">
          <Search className="h-5 w-5 text-gray-700" />
          <h2 className="text-lg font-semibold text-gray-950">Search or Request a Word</h2>
        </div>
        <form onSubmit={onSearch} className="flex flex-col gap-3 sm:flex-row">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search a contractual or legal word"
            className="min-h-10 flex-1"
          />
          <Button type="submit" disabled={searching || !query.trim()} className="min-w-28">
            {searching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
            Search
          </Button>
        </form>

        <div className="mt-4">
          {searchResult ? (
            <WordCard word={searchResult} />
          ) : searchMessage ? (
            <div className="rounded-md border border-gray-200 bg-white p-4 text-sm text-gray-700">
              {searchMessage}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
};

const publicationDateFor = (word: LegalWord | LegalWordPublishedItem) =>
  "display_publication_date" in word ? word.display_publication_date : word.published_date;

const WordCard = ({ word, index }: { word: LegalWord | LegalWordPublishedItem; index?: number }) => {
  const publicationDate = publicationDateFor(word);
  const badge = "publication_badge" in word ? word.publication_badge : null;
  return (
    <Card className="h-full rounded-md border-gray-200 shadow-sm">
      <CardHeader className="space-y-2 pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-xl text-gray-950">
              {index ? `${index}. ` : ""}
              {word.word}
            </CardTitle>
            {publicationDate ? (
              <p className="mt-1 text-xs text-gray-500">Published {formatDate(publicationDate)}</p>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            {badge ? (
              <Badge
                variant="secondary"
                className={`rounded-md ${
                  badge === "New" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
                }`}
              >
                {badge}
              </Badge>
            ) : null}
            {word.category ? (
              <Badge variant="outline" className="rounded-md">
                {word.category}
              </Badge>
            ) : null}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <div className="mb-1 text-xs font-semibold uppercase text-gray-500">Meaning</div>
          <p className="leading-6 text-gray-800">{word.meaning || "Meaning pending admin review."}</p>
        </div>
        <div>
          <div className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase text-gray-500">
            <Tag className="h-3.5 w-3.5" />
            Useful Synonyms
          </div>
          {word.synonyms.length ? (
            <div className="flex flex-wrap gap-2">
              {word.synonyms.map((synonym) => (
                <Badge key={synonym} variant="outline" className="rounded-md bg-gray-50">
                  {synonym}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-gray-500">No synonyms recorded.</p>
          )}
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold uppercase text-gray-500">Example in Contractual Letter</div>
          <p className="rounded-md border border-gray-200 bg-gray-50 p-3 leading-6 text-gray-800">
            {word.example_sentence || "Example pending admin review."}
          </p>
        </div>
      </CardContent>
    </Card>
  );
};

export default LegalWordsPage;
