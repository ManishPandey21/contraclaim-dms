import { useEffect, useState } from "react";
import { Check, Link2, Linkedin, Mail } from "lucide-react";

/**
 * Social sharing for an individual article or video.
 *
 * Plain share URLs, no third-party scripts or tracking pixels: nothing here
 * loads code from another origin, so adding sharing does not change the page's
 * privacy or performance profile.
 */
export function ShareLinks({
  url,
  title,
  className,
}: {
  url: string;
  title: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const encodedUrl = encodeURIComponent(url);
  const encodedTitle = encodeURIComponent(title);

  const targets = [
    {
      label: "Share on LinkedIn",
      href: `https://www.linkedin.com/sharing/share-offsite/?url=${encodedUrl}`,
      icon: Linkedin,
    },
    {
      label: "Share by email",
      href: `mailto:?subject=${encodedTitle}&body=${encodedUrl}`,
      icon: Mail,
    },
  ];

  const copyLink = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
        setCopied(true);
      }
    } catch {
      // Clipboard access can be denied; the URL is visible in the address bar,
      // so failing quietly is better than an error toast here.
      setCopied(false);
    }
  };

  const buttonClass =
    "inline-flex h-10 w-10 items-center justify-center rounded-full border border-ink/15 text-ink/70 transition hover:border-brand hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2";

  return (
    <div className={className}>
      <div className="flex items-center gap-3">
        <span className="text-sm font-semibold text-ink/60">Share</span>
        <ul className="flex items-center gap-2">
          {targets.map((target) => (
            <li key={target.label}>
              <a
                href={target.href}
                target="_blank"
                rel="noopener noreferrer"
                className={buttonClass}
                aria-label={target.label}
              >
                <target.icon className="h-4 w-4" aria-hidden="true" />
              </a>
            </li>
          ))}
          <li>
            <button
              type="button"
              onClick={copyLink}
              className={buttonClass}
              aria-label={copied ? "Link copied" : "Copy link"}
            >
              {copied ? (
                <Check className="h-4 w-4" aria-hidden="true" />
              ) : (
                <Link2 className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          </li>
        </ul>
        <span aria-live="polite" className="sr-only">
          {copied ? "Link copied to clipboard" : ""}
        </span>
      </div>
    </div>
  );
}

export default ShareLinks;
