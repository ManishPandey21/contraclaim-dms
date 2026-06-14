import { useState, useEffect, useCallback, useMemo } from "react";

export interface VirtualScrollingOptions {
  itemHeight: number;
  containerHeight: number;
  overscan?: number;
  totalItems: number;
}

export interface VirtualScrollingResult {
  startIndex: number;
  endIndex: number;
  visibleItems: number;
  offsetY: number;
  totalHeight: number;
  scrollToIndex: (index: number) => void;
}

export const useVirtualScrolling = (
  options: VirtualScrollingOptions
): VirtualScrollingResult => {
  const { itemHeight, containerHeight, overscan = 5, totalItems } = options;

  const [scrollTop, setScrollTop] = useState(0);

  const visibleItems = Math.ceil(containerHeight / itemHeight);
  const totalHeight = totalItems * itemHeight;

  const startIndex = useMemo(() => {
    const index = Math.floor(scrollTop / itemHeight);
    return Math.max(0, index - overscan);
  }, [scrollTop, itemHeight, overscan]);

  const endIndex = useMemo(() => {
    const index = Math.min(
      totalItems - 1,
      Math.floor((scrollTop + containerHeight) / itemHeight) + overscan
    );
    return Math.max(startIndex, index);
  }, [
    scrollTop,
    containerHeight,
    itemHeight,
    overscan,
    totalItems,
    startIndex,
  ]);

  const offsetY = startIndex * itemHeight;

  const scrollToIndex = useCallback(
    (index: number) => {
      const targetScrollTop = index * itemHeight;
      setScrollTop(
        Math.max(0, Math.min(targetScrollTop, totalHeight - containerHeight))
      );
    },
    [itemHeight, totalHeight, containerHeight]
  );

  return {
    startIndex,
    endIndex,
    visibleItems,
    offsetY,
    totalHeight,
    scrollToIndex,
  };
};

export interface InfiniteScrollOptions {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  threshold?: number;
}

export const useInfiniteScroll = (
  containerRef: React.RefObject<HTMLElement>,
  options: InfiniteScrollOptions
) => {
  const {
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    threshold = 100,
  } = options;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

      if (
        distanceFromBottom < threshold &&
        hasNextPage &&
        !isFetchingNextPage
      ) {
        fetchNextPage();
      }
    };

    container.addEventListener("scroll", handleScroll);
    return () => container.removeEventListener("scroll", handleScroll);
  }, [containerRef, hasNextPage, isFetchingNextPage, fetchNextPage, threshold]);
};

export interface LazyLoadingOptions<T> {
  items: T[];
  pageSize: number;
  loadMore: () => Promise<void>;
  hasMore: boolean;
  isLoading: boolean;
}

export const useLazyLoading = <T>(options: LazyLoadingOptions<T>) => {
  const { items, pageSize, loadMore, hasMore, isLoading } = options;
  const [visibleCount, setVisibleCount] = useState(pageSize);

  const visibleItems = useMemo(() => {
    return items.slice(0, visibleCount);
  }, [items, visibleCount]);

  const loadMoreItems = useCallback(async () => {
    if (isLoading || !hasMore) return;

    if (visibleCount >= items.length && hasMore) {
      await loadMore();
    } else {
      setVisibleCount((prev) => Math.min(prev + pageSize, items.length));
    }
  }, [visibleCount, items.length, pageSize, hasMore, isLoading, loadMore]);

  const reset = useCallback(() => {
    setVisibleCount(pageSize);
  }, [pageSize]);

  return {
    visibleItems,
    loadMoreItems,
    hasMoreToShow: visibleCount < items.length || hasMore,
    isLoadingMore: isLoading,
    reset,
  };
};

// Hook for optimized search with debouncing
export const useOptimizedSearch = (
  searchFunction: (query: string) => Promise<any>,
  debounceMs: number = 300
) => {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!query.trim()) {
      setResults(null);
      return;
    }

    const timeoutId = setTimeout(async () => {
      setIsSearching(true);
      setError(null);

      try {
        const searchResults = await searchFunction(query);
        setResults(searchResults);
      } catch (err) {
        console.error("Search error:", err);
        setError("Search failed. Please try again.");
      } finally {
        setIsSearching(false);
      }
    }, debounceMs);

    return () => clearTimeout(timeoutId);
  }, [query, searchFunction, debounceMs]);

  const clearSearch = useCallback(() => {
    setQuery("");
    setResults(null);
    setError(null);
  }, []);

  return {
    query,
    setQuery,
    results,
    isSearching,
    error,
    clearSearch,
  };
};

// Hook for managing selection state efficiently
export const useSelection = <T extends { _id: string }>(items: T[]) => {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const selectedItems = useMemo(() => {
    return items.filter((item) => selectedIds.has(item._id));
  }, [items, selectedIds]);

  const isSelected = useCallback(
    (id: string) => {
      return selectedIds.has(id);
    },
    [selectedIds]
  );

  const toggleSelection = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(items.map((item) => item._id)));
  }, [items]);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const selectRange = useCallback(
    (startId: string, endId: string) => {
      const startIndex = items.findIndex((item) => item._id === startId);
      const endIndex = items.findIndex((item) => item._id === endId);

      if (startIndex === -1 || endIndex === -1) return;

      const [start, end] = [
        Math.min(startIndex, endIndex),
        Math.max(startIndex, endIndex),
      ];
      const rangeIds = items.slice(start, end + 1).map((item) => item._id);

      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        rangeIds.forEach((id) => newSet.add(id));
        return newSet;
      });
    },
    [items]
  );

  return {
    selectedItems,
    selectedIds,
    isSelected,
    toggleSelection,
    selectAll,
    clearSelection,
    selectRange,
    hasSelection: selectedIds.size > 0,
    selectionCount: selectedIds.size,
  };
};

// Hook for caching API responses
export const useApiCache = <T>(
  key: string,
  fetcher: () => Promise<T>,
  options: {
    ttl?: number; // Time to live in milliseconds
    staleWhileRevalidate?: boolean;
  } = {}
) => {
  const { ttl = 5 * 60 * 1000, staleWhileRevalidate = true } = options; // Default 5 minutes

  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [lastFetch, setLastFetch] = useState<number>(0);

  const isStale = useMemo(() => {
    return Date.now() - lastFetch > ttl;
  }, [lastFetch, ttl]);

  const fetchData = useCallback(
    async (force = false) => {
      if (!force && data && !isStale) {
        return data;
      }

      if (!staleWhileRevalidate || !data) {
        setIsLoading(true);
      }

      setError(null);

      try {
        const result = await fetcher();
        setData(result);
        setLastFetch(Date.now());
        return result;
      } catch (err) {
        const error = err instanceof Error ? err : new Error("Fetch failed");
        setError(error);
        throw error;
      } finally {
        setIsLoading(false);
      }
    },
    [data, isStale, staleWhileRevalidate, fetcher]
  );

  useEffect(() => {
    if (!data) {
      fetchData();
    }
  }, [data, fetchData]);

  const invalidate = useCallback(() => {
    setLastFetch(0);
  }, []);

  const refetch = useCallback(() => {
    return fetchData(true);
  }, [fetchData]);

  return {
    data,
    isLoading,
    error,
    isStale,
    refetch,
    invalidate,
  };
};
