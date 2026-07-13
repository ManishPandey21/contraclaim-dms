import React, { Component, ErrorInfo, ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { logError } from "@/lib/error-logger";
import { isChunkLoadError } from "@/lib/lazyWithRetry";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    logError(error, {
      scope: "ErrorBoundary",
      action: "componentDidCatch",
      metadata: { componentStack: errorInfo.componentStack },
    });
  }

  private handleRetry = () => {
    // A stale-deploy chunk failure can't be recovered by re-rendering the same
    // now-missing module — only a full reload fetches the current assets.
    if (isChunkLoadError(this.state.error)) {
      window.location.reload();
      return;
    }
    this.setState({ hasError: false, error: null });
  };

  public render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // A failed chunk load is almost always a stale deploy, not a real fault:
      // show a recovery-oriented message and a Reload action instead of the
      // raw "Failed to fetch dynamically imported module" string.
      const chunkError = isChunkLoadError(this.state.error);

      return (
        <div className="flex items-center justify-center min-h-screen bg-background">
          <div className="w-full max-w-md p-6">
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Something went wrong</AlertTitle>
              <AlertDescription>
                {chunkError
                  ? "A newer version of the app is available. Reloading will fix this."
                  : this.state.error?.message || "An unexpected error occurred"}
              </AlertDescription>
            </Alert>
            <div className="mt-4 flex justify-center">
              <Button onClick={this.handleRetry}>
                {chunkError ? "Reload" : "Try Again"}
              </Button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

// Custom hook for error boundary
export const withErrorBoundary = <P extends object>(
  WrappedComponent: React.ComponentType<P>,
  fallback?: ReactNode
) => {
  return function WithErrorBoundary(props: P) {
    return (
      <ErrorBoundary fallback={fallback}>
        <WrappedComponent {...props} />
      </ErrorBoundary>
    );
  };
};

// Error Fallback Component
export const ErrorFallback: React.FC<{
  error?: Error;
  resetError?: () => void;
}> = ({ error, resetError }) => {
  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <div className="w-full max-w-md p-6">
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>
            {error?.message || "An unexpected error occurred"}
          </AlertDescription>
        </Alert>
        {resetError && (
          <div className="mt-4 flex justify-center">
            <Button onClick={resetError}>Try Again</Button>
          </div>
        )}
      </div>
    </div>
  );
};

// HOC for wrapping components with error boundary
export const withPartiesErrorBoundary = <P extends object>(
  WrappedComponent: React.ComponentType<P>
) => {
  return withErrorBoundary(WrappedComponent, <ErrorFallback />);
};
