import React from "react";
import clsx from "clsx";

type DivProps = React.HTMLAttributes<HTMLDivElement>;

export const Card = React.forwardRef<HTMLDivElement, DivProps>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={clsx(
        "bg-white border border-gray-200 rounded-lg shadow-sm",
        className
      )}
      {...props}
    />
  )
);
Card.displayName = "Card";

export const CardHeader = ({ className, children, ...props }: DivProps) => (
  <div
    className={clsx(
      "px-5 py-4 border-b border-gray-200 flex items-start justify-between gap-3",
      className
    )}
    {...props}
  >
    {children}
  </div>
);

export const CardTitle = ({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) => (
  <h3 className={clsx("text-lg font-semibold", className)} {...props}>
    {children}
  </h3>
);

export const CardDescription = ({
  className,
  children,
  ...props
}: DivProps) => (
  <div className={clsx("text-sm text-gray-600", className)} {...props}>
    {children}
  </div>
);

export const CardContent = ({ className, children, ...props }: DivProps) => (
  <div className={clsx("px-5 py-4", className)} {...props}>
    {children}
  </div>
);

export const CardFooter = ({ className, children, ...props }: DivProps) => (
  <div
    className={clsx(
      "px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2",
      className
    )}
    {...props}
  >
    {children}
  </div>
);

const DefaultExport = {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
};

export default DefaultExport;
