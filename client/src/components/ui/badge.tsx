import React from "react";
import clsx from "clsx";

export type BadgeProps = {
  children: React.ReactNode;
  variant?:
    | "primary"
    | "success"
    | "warning"
    | "danger"
    | "neutral"
    | "outline";
  className?: string;
};

const styles: Record<NonNullable<BadgeProps["variant"]>, string> = {
  primary: "bg-blue-100 text-blue-700 ring-1 ring-inset ring-blue-200",
  success: "bg-emerald-100 text-emerald-700 ring-1 ring-inset ring-emerald-200",
  warning: "bg-amber-100 text-amber-800 ring-1 ring-inset ring-amber-200",
  danger: "bg-red-100 text-red-700 ring-1 ring-inset ring-red-200",
  neutral: "bg-gray-100 text-gray-700 ring-1 ring-inset ring-gray-200",
  outline: "bg-white text-gray-700 ring-1 ring-inset ring-gray-300",
};

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = "neutral",
  className,
}) => {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        styles[variant],
        className
      )}
    >
      {children}
    </span>
  );
};

export default Badge;
