// A failed poll is asked again after 1 s, 2 s, 4 s, ... up to 10 s while the page still has a reason to poll: polling
// never stops for good after one failed answer and never spins against a failing server.

export const RETRY_FIRST_MS = 1000;
export const RETRY_MAX_MS = 10_000;

/** The wait before the next try after `failures` failed tries in a row (1, 2, 3, ...). */
export function retryDelay(failures: number): number {
  return Math.min(RETRY_FIRST_MS * 2 ** Math.max(0, failures - 1), RETRY_MAX_MS);
}
