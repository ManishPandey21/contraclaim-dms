import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import { listSubTags } from "@/services/tags-api";

export type SubtagOption = { value: string; label: string; tagId: string };

/**
 * Subtag options for the tag a user selects, loaded lazily and at most once
 * per tag for the life of the component (Document Viewer).
 *
 * The record of loaded tags is explicit, not inferred from the options:
 * a tag with zero subtags leaves no option behind, so "no options for A"
 * cannot tell "loaded, empty" from "never loaded" and would refetch on every
 * selection. Each fetch draws on the per-user Tags read budget.
 */
export function useSubtagOptions() {
  const [availableSubtags, setAvailableSubtags] = useState<SubtagOption[]>([]);
  const [isLoadingSubtags, setIsLoadingSubtags] = useState(false);
  const loadedTagIds = useRef<Set<string>>(new Set());
  // Bumped by resetSubtags: a request started before a reset must not write
  // its options, or release a claim made after it, when it settles.
  const generation = useRef(0);

  /**
   * Forget every loaded tag, e.g. when the options themselves are cleared.
   * Any request still in flight belongs to the old generation and will write
   * nothing when it settles, so nothing is loading any more.
   */
  const resetSubtags = useCallback(() => {
    generation.current += 1;
    loadedTagIds.current.clear();
    setAvailableSubtags([]);
    setIsLoadingSubtags(false);
  }, []);

  const fetchSubtags = useCallback(
    async (tagId: string) => {
      if (!tagId) {
        resetSubtags();
        return;
      }
      // Claimed before the request, so a re-render cannot start a second one.
      if (loadedTagIds.current.has(tagId)) return;
      loadedTagIds.current.add(tagId);
      const startedIn = generation.current;

      setIsLoadingSubtags(true);
      try {
        const subtagArray = await listSubTags(tagId, { limit: 200 });
        if (startedIn !== generation.current) return;
        setAvailableSubtags((prevSubtags) => {
          // Filter out subtags belonging to this tagId before adding new ones
          const otherSubtags = prevSubtags.filter((subtag) => subtag.tagId !== tagId);
          const newSubtags = subtagArray
            .map((subtag) => ({ value: subtag._id, label: subtag.name, tagId }))
            .filter((subtag) => subtag.value && subtag.label);
          return [...otherSubtags, ...newSubtags];
        });
      } catch (error) {
        console.error("Error fetching subtags:", error);
        if (startedIn !== generation.current) return;
        // Not loaded after all: selecting the tag again may retry.
        loadedTagIds.current.delete(tagId);
        toast.error("Failed to fetch subtags", {
          description: "Please try again later",
        });
        setAvailableSubtags((prevSubtags) =>
          prevSubtags.filter((subtag) => subtag.tagId !== tagId)
        );
      } finally {
        // A request from before a reset must not end a newer one's spinner.
        if (startedIn === generation.current) setIsLoadingSubtags(false);
      }
    },
    [resetSubtags]
  );

  return { availableSubtags, isLoadingSubtags, fetchSubtags, resetSubtags };
}
