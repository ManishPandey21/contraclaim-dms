import React, { useEffect, useCallback, useRef, useState } from "react";

// Simple debounce implementation
const debounce = <T extends (...args: any[]) => any>(
  func: T,
  delay: number
): ((...args: Parameters<T>) => void) => {
  let timeoutId: NodeJS.Timeout;
  return (...args: Parameters<T>) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => func(...args), delay);
  };
};

// Performance monitoring hook
export const usePerformanceMonitor = () => {
  const [metrics, setMetrics] = useState({
    renderTime: 0,
    memoryUsage: 0,
    componentCount: 0,
  });

  const startTime = useRef<number>(0);

  useEffect(() => {
    startTime.current = performance.now();

    return () => {
      const endTime = performance.now();
      const renderTime = endTime - startTime.current;

      setMetrics((prev) => ({
        ...prev,
        renderTime,
        memoryUsage: (performance as any).memory?.usedJSHeapSize || 0,
      }));
    };
  }, []);

  return metrics;
};

// Debounced callback hook
export const useDebouncedCallback = <T extends (...args: any[]) => any>(
  callback: T,
  delay: number = 300
): T => {
  const debouncedCallback = useCallback(debounce(callback, delay), [
    callback,
    delay,
  ]);

  return debouncedCallback as T;
};

// Throttled callback hook
export const useThrottledCallback = <T extends (...args: any[]) => any>(
  callback: T,
  delay: number = 100
): T => {
  const lastRun = useRef<number>(0);

  const throttledCallback = useCallback(
    (...args: any[]) => {
      const now = Date.now();
      if (now - lastRun.current >= delay) {
        lastRun.current = now;
        return callback(...args);
      }
    },
    [callback, delay]
  );

  return throttledCallback as T;
};

// Intersection Observer hook for lazy loading
export const useIntersectionObserver = (
  options: IntersectionObserverInit = {}
) => {
  const [isIntersecting, setIsIntersecting] = useState(false);
  const [hasIntersected, setHasIntersected] = useState(false);
  const elementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsIntersecting(entry.isIntersecting);
        if (entry.isIntersecting && !hasIntersected) {
          setHasIntersected(true);
        }
      },
      {
        threshold: 0.1,
        rootMargin: "50px",
        ...options,
      }
    );

    observer.observe(element);

    return () => {
      observer.unobserve(element);
    };
  }, [hasIntersected, options]);

  return { elementRef, isIntersecting, hasIntersected };
};

// Image lazy loading hook
export const useLazyImage = (src: string, placeholder?: string) => {
  const [imageSrc, setImageSrc] = useState(placeholder || "");
  const [isLoaded, setIsLoaded] = useState(false);
  const [isError, setIsError] = useState(false);
  const { elementRef, hasIntersected } = useIntersectionObserver();

  useEffect(() => {
    if (!hasIntersected) return;

    const img = new Image();
    img.onload = () => {
      setImageSrc(src);
      setIsLoaded(true);
    };
    img.onerror = () => {
      setIsError(true);
    };
    img.src = src;
  }, [src, hasIntersected]);

  return { elementRef, imageSrc, isLoaded, isError };
};

// Component lazy loading hook
export const useLazyComponent = <T extends React.ComponentType<any>>(
  importFunc: () => Promise<{ default: T }>
) => {
  const [Component, setComponent] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const loadComponent = useCallback(async () => {
    if (Component || isLoading) return;

    setIsLoading(true);
    setError(null);

    try {
      const module = await importFunc();
      setComponent(() => module.default);
    } catch (err) {
      setError(err as Error);
    } finally {
      setIsLoading(false);
    }
  }, [Component, isLoading, importFunc]);

  return { Component, isLoading, error, loadComponent };
};

// Memory usage monitoring hook
export const useMemoryMonitor = () => {
  const [memoryInfo, setMemoryInfo] = useState({
    usedJSHeapSize: 0,
    totalJSHeapSize: 0,
    jsHeapSizeLimit: 0,
  });

  useEffect(() => {
    const updateMemoryInfo = () => {
      if ((performance as any).memory) {
        setMemoryInfo({
          usedJSHeapSize: (performance as any).memory.usedJSHeapSize,
          totalJSHeapSize: (performance as any).memory.totalJSHeapSize,
          jsHeapSizeLimit: (performance as any).memory.jsHeapSizeLimit,
        });
      }
    };

    updateMemoryInfo();
    const interval = setInterval(updateMemoryInfo, 5000); // Update every 5 seconds

    return () => clearInterval(interval);
  }, []);

  return memoryInfo;
};

// Bundle size analyzer (development only)
export const useBundleAnalyzer = () => {
  const [bundleInfo, setBundleInfo] = useState({
    chunks: [] as string[],
    totalSize: 0,
  });

  useEffect(() => {
    if (process.env.NODE_ENV === "development") {
      // Analyze loaded chunks
      const scripts = Array.from(document.querySelectorAll("script[src]"));
      const chunks = scripts
        .map((script) => (script as HTMLScriptElement).src)
        .filter((src) => src.includes("chunk") || src.includes("vendor"));

      setBundleInfo({
        chunks,
        totalSize: chunks.length,
      });
    }
  }, []);

  return bundleInfo;
};

// Performance optimization utilities
export const performanceUtils = {
  // Measure component render time
  measureRender: (componentName: string, renderFn: () => void) => {
    const start = performance.now();
    renderFn();
    const end = performance.now();
    console.log(`${componentName} render time: ${end - start}ms`);
  },

  // Preload critical resources
  preloadResource: (href: string, as: string = "script") => {
    const link = document.createElement("link");
    link.rel = "preload";
    link.href = href;
    link.as = as;
    document.head.appendChild(link);
  },

  // Prefetch next page resources
  prefetchResource: (href: string) => {
    const link = document.createElement("link");
    link.rel = "prefetch";
    link.href = href;
    document.head.appendChild(link);
  },

  // Check if user prefers reduced motion
  prefersReducedMotion: () => {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  },

  // Get connection information
  getConnectionInfo: () => {
    const connection = (navigator as any).connection;
    if (!connection) return null;

    return {
      effectiveType: connection.effectiveType,
      downlink: connection.downlink,
      rtt: connection.rtt,
      saveData: connection.saveData,
    };
  },

  // Optimize images based on connection
  getOptimalImageSize: (baseSize: number) => {
    const connection = performanceUtils.getConnectionInfo();
    if (!connection) return baseSize;

    if (connection.saveData || connection.effectiveType === "slow-2g") {
      return Math.floor(baseSize * 0.5);
    }
    if (connection.effectiveType === "2g") {
      return Math.floor(baseSize * 0.7);
    }
    if (connection.effectiveType === "3g") {
      return Math.floor(baseSize * 0.85);
    }
    return baseSize;
  },
};

// React.memo with custom comparison
export const createMemoComponent = <P extends object>(
  Component: React.ComponentType<P>,
  propsAreEqual?: (prevProps: P, nextProps: P) => boolean
) => {
  return React.memo(Component, propsAreEqual);
};

// HOC for performance monitoring
export const withPerformanceMonitoring = <P extends object>(
  WrappedComponent: React.ComponentType<P>,
  componentName: string
) => {
  return React.memo((props: P) => {
    const metrics = usePerformanceMonitor();

    useEffect(() => {
      if (process.env.NODE_ENV === "development") {
        console.log(`${componentName} performance:`, metrics);
      }
    }, [metrics]);

    return React.createElement(WrappedComponent, props);
  });
};

export default {
  usePerformanceMonitor,
  useDebouncedCallback,
  useThrottledCallback,
  useIntersectionObserver,
  useLazyImage,
  useLazyComponent,
  useMemoryMonitor,
  useBundleAnalyzer,
  performanceUtils,
  createMemoComponent,
  withPerformanceMonitoring,
};
