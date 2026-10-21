/** The purple chip of every LLM output (prototype: low weight, for reference). */
export function LlmBadge({ children }: { children: string }) {
  return (
    <span className="llm-chip" data-testid="llm-chip">
      {children}
    </span>
  );
}
