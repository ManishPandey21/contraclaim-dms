import React from "react";
import clsx from "clsx";

type Props = {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  className?: string;
};

const PageHeader: React.FC<Props> = ({
  icon,
  title,
  description,
  className,
}) => {
  return (
    <div
      className={clsx(
        "mb-6 flex items-start gap-3 border-b border-gray-200 pb-4",
        className
      )}
    >
      {icon ? (
        <div className="mt-0.5 inline-flex h-8 w-8 items-center justify-center rounded-full bg-blue-50 text-blue-600">
          {icon}
        </div>
      ) : null}
      <div>
        <h1 className="text-2xl font-semibold leading-tight">{title}</h1>
        {description ? (
          <p className="mt-1 text-sm text-gray-600">{description}</p>
        ) : null}
      </div>
    </div>
  );
};

export default PageHeader;
