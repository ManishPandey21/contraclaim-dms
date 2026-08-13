/**
 * Date formatting utilities with user preferences support
 */

export type DateFormat = "dd-mm-yyyy" | "dd/mm/yyyy" | "mm/dd/yyyy" | "yyyy-mm-dd";

/** The house format for contractual dates: day-first, hyphenated, unambiguous. */
export const DEFAULT_DATE_FORMAT: DateFormat = "dd-mm-yyyy";

const SUPPORTED_FORMATS: DateFormat[] = ["dd-mm-yyyy", "dd/mm/yyyy", "mm/dd/yyyy", "yyyy-mm-dd"];

/**
 * Get the user's preferred date format from localStorage
 * Defaults to DEFAULT_DATE_FORMAT if not set or unrecognised
 */
export const getUserDateFormat = (): DateFormat => {
  const stored = localStorage.getItem("dateFormat");
  if (stored && (SUPPORTED_FORMATS as string[]).includes(stored)) {
    return stored as DateFormat;
  }
  return DEFAULT_DATE_FORMAT;
};

/**
 * Set the user's preferred date format in localStorage
 */
export const setUserDateFormat = (format: DateFormat): void => {
  localStorage.setItem("dateFormat", format);
};

/**
 * Format a date string or Date object according to user preference
 * @param date - Date string (ISO format) or Date object
 * @param format - Optional format override, otherwise uses user preference
 * @returns Formatted date string
 */
export const formatDate = (
  date: string | Date | null | undefined,
  format?: DateFormat
): string => {
  if (!date) return "N/A";

  try {
    const dateObj = typeof date === "string" ? new Date(date) : date;

    // Check if date is valid
    if (isNaN(dateObj.getTime())) {
      return "Invalid Date";
    }

    const selectedFormat = format || getUserDateFormat();

    const day = String(dateObj.getDate()).padStart(2, "0");
    const month = String(dateObj.getMonth() + 1).padStart(2, "0");
    const year = dateObj.getFullYear();

    switch (selectedFormat) {
      case "dd-mm-yyyy":
        return `${day}-${month}-${year}`;
      case "dd/mm/yyyy":
        return `${day}/${month}/${year}`;
      case "mm/dd/yyyy":
        return `${month}/${day}/${year}`;
      case "yyyy-mm-dd":
        return `${year}-${month}-${day}`;
      default:
        return `${day}-${month}-${year}`;
    }
  } catch (error) {
    console.error("Error formatting date:", error);
    return "Invalid Date";
  }
};

/**
 * Format a date with time according to user preference
 * @param date - Date string (ISO format) or Date object
 * @param format - Optional format override, otherwise uses user preference
 * @returns Formatted date and time string
 */
export const formatDateTime = (
  date: string | Date | null | undefined,
  format?: DateFormat
): string => {
  if (!date) return "N/A";

  try {
    const dateObj = typeof date === "string" ? new Date(date) : date;

    // Check if date is valid
    if (isNaN(dateObj.getTime())) {
      return "Invalid Date";
    }

    const dateStr = formatDate(dateObj, format);
    const hours = String(dateObj.getHours()).padStart(2, "0");
    const minutes = String(dateObj.getMinutes()).padStart(2, "0");

    return `${dateStr} ${hours}:${minutes}`;
  } catch (error) {
    console.error("Error formatting date time:", error);
    return "Invalid Date";
  }
};

/**
 * Get a human-readable label for a date format
 */
export const getDateFormatLabel = (format: DateFormat): string => {
  switch (format) {
    case "dd-mm-yyyy":
      return "DD-MM-YYYY (Day-Month-Year)";
    case "dd/mm/yyyy":
      return "DD/MM/YYYY (Day/Month/Year)";
    case "mm/dd/yyyy":
      return "MM/DD/YYYY (Month/Day/Year)";
    case "yyyy-mm-dd":
      return "YYYY-MM-DD (Year-Month-Day)";
    default:
      return format;
  }
};

/**
 * Get all available date formats
 */
export const getAvailableDateFormats = (): DateFormat[] => {
  return [...SUPPORTED_FORMATS];
};
