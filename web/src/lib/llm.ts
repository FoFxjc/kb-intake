/** What the optional LLM is sent, as the UI tells the user before it happens (src/kb_intake/llm.py). */

export function hostOf(baseUrl: string): string {
  try {
    return new URL(baseUrl).host || baseUrl;
  } catch {
    return baseUrl;
  }
}

/** llm.summary_input: SUMMARY_MAX_CHARS */
export const SUMMARY_SENDS = "the declaration and the start of every page's text (up to 24,000 characters)";
