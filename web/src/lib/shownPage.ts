// pdf.js (6.4.299) keeps what it needed to render a page (its operator list, fonts and images) until the page is
// cleaned up or the document destroyed: on a long PDF every page visited would stay in memory. The viewer tracks
// the page it shows and cleans up the one before once another page is shown.

export interface Cleanable {
  cleanup(): unknown;
}

export class ShownPage<P extends Cleanable = Cleanable> {
  private current: P | null = null;

  /** `page` is rendered now: the page shown before (another one) is cleaned up. Call it once the render before is
   * done or cancelled, so pdf.js does not keep it for a render in flight. */
  show(page: P): void {
    if (this.current && this.current !== page) this.current.cleanup();
    this.current = page;
  }

  /** The viewer goes away: the shown page is cleaned up too. */
  release(): void {
    this.current?.cleanup();
    this.current = null;
  }
}
