import React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface LoadingSpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
  message?: string;
}

const sizeClasses = {
  sm: "h-4 w-4",
  md: "h-8 w-8",
  lg: "h-12 w-12",
};

const containerSizeClasses = {
  sm: "min-h-[100px]",
  md: "min-h-[200px]",
  lg: "min-h-[300px]",
};

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  size = "md",
  className,
  message = "Loading...",
}) => {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center",
        containerSizeClasses[size],
        className
      )}
    >
      <Loader2 className={cn("animate-spin text-primary", sizeClasses[size])} />
      {message && (
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
      )}
    </div>
  );
};

interface LoadingOverlayProps extends LoadingSpinnerProps {
  isLoading: boolean;
  children: React.ReactNode;
}

export const LoadingOverlay: React.FC<LoadingOverlayProps> = ({
  isLoading,
  children,
  ...spinnerProps
}) => {
  if (!isLoading) return <>{children}</>;

  return (
    <div className="relative">
      <div className="absolute inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center">
        <LoadingSpinner {...spinnerProps} />
      </div>
      <div className="opacity-50 pointer-events-none">{children}</div>
    </div>
  );
};

interface LoadingButtonProps {
  loading: boolean;
  children: React.ReactNode;
  loadingText?: string;
  [key: string]: any; // For other button props
}

export const LoadingButton: React.FC<LoadingButtonProps> = ({
  loading,
  children,
  loadingText = "Loading...",
  ...props
}) => {
  return (
    <button
      disabled={loading}
      className={cn(
        "inline-flex items-center justify-center",
        loading && "opacity-70 cursor-not-allowed"
      )}
      {...props}
    >
      {loading ? (
        <>
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          {loadingText}
        </>
      ) : (
        children
      )}
    </button>
  );
};

// Skeleton loader for party cards
export const PartyCardSkeleton = () => {
  return (
    <div className="p-4 border rounded-lg animate-pulse">
      <div className="flex justify-between items-start">
        <div className="flex items-center gap-2">
          <div className="h-4 w-4 bg-muted rounded" />
          <div className="h-4 w-32 bg-muted rounded" />
        </div>
        <div className="h-6 w-20 bg-muted rounded" />
      </div>
      <div className="mt-2 space-y-2">
        <div className="h-4 w-40 bg-muted rounded" />
        <div className="h-4 w-36 bg-muted rounded" />
      </div>
    </div>
  );
};

// Skeleton loader for party list
export const PartyListSkeleton = () => {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <PartyCardSkeleton key={i} />
      ))}
    </div>
  );
};

// Skeleton loader for party details
export const PartyDetailsSkeleton = () => {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="flex justify-between items-center">
        <div className="h-6 w-48 bg-muted rounded" />
        <div className="flex gap-2">
          <div className="h-8 w-8 bg-muted rounded" />
          <div className="h-8 w-8 bg-muted rounded" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-2">
            <div className="h-4 w-20 bg-muted rounded" />
            <div className="h-10 w-full bg-muted rounded" />
          </div>
        ))}
      </div>
      <div className="h-px w-full bg-muted" />
      <div className="space-y-4">
        <div className="h-6 w-40 bg-muted rounded" />
        <div className="space-y-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="h-12 w-full bg-muted rounded" />
          ))}
        </div>
      </div>
    </div>
  );
};
