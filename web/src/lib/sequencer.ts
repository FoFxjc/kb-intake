// Latest request wins (plan, Review Focus 2): each start() aborts the request before it, and a result is applied
// only while its ticket is still the latest one, so an older answer arriving after a newer one never overwrites
// the newer one (an abort the server or the browser does not honour changes nothing).

export interface Ticket {
  readonly signal: AbortSignal;
  /** true while no later start() (or cancel()) happened */
  isLatest(): boolean;
}

export class Sequencer {
  private seq = 0;
  private ctrl: AbortController | null = null;

  /** A new request: aborts the previous one; the previous tickets stop being the latest. */
  start(): Ticket {
    this.ctrl?.abort();
    const ctrl = new AbortController();
    this.ctrl = ctrl;
    const id = ++this.seq;
    return { signal: ctrl.signal, isLatest: () => id === this.seq };
  }

  /** A follow-up of the latest request (e.g. the next page of the same list): not aborted by it, stale with it. */
  follow(): Ticket {
    const id = this.seq;
    const signal = this.ctrl?.signal ?? new AbortController().signal;
    return { signal, isLatest: () => id === this.seq };
  }

  cancel(): void {
    this.ctrl?.abort();
    this.ctrl = null;
    this.seq++;
  }

  /** Runs `fn` as the latest request and hands its result to `apply` only if it is still the latest when it
   * arrives. Resolves to whether it was applied; an abort is not an error. */
  async run<T>(fn: (signal: AbortSignal) => Promise<T>, apply: (value: T) => void): Promise<boolean> {
    const t = this.start();
    try {
      const value = await fn(t.signal);
      if (!t.isLatest()) return false;
      apply(value);
      return true;
    } catch (e) {
      if (!t.isLatest() || (e instanceof DOMException && e.name === 'AbortError')) return false;
      throw e;
    }
  }
}
