import { blocksToText, type MarkdownBlock } from "./markdown";

/**
 * Words per minute used for the reading estimate. 200 is a conservative rate
 * for dense professional prose, which is what this content is.
 */
export const WORDS_PER_MINUTE = 200;

export function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

/** Estimated reading time in whole minutes, never less than 1. */
export function estimateReadingMinutes(blocks: MarkdownBlock[]): number {
  const words = countWords(blocksToText(blocks));
  return Math.max(1, Math.ceil(words / WORDS_PER_MINUTE));
}
