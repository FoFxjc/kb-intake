import { ShownPage } from '../src/lib/shownPage';
import { expect, test } from './fixtures';

// pdf.js keeps the operator list of every page rendered until the page is cleaned up or the document destroyed:
// the page viewer cleans up the page it showed before once another page is shown (PdfPage).

test.describe('the shown PDF page (unit)', () => {
  test('the page shown before is cleaned up once another page is shown, never the shown one', () => {
    const cleaned: string[] = [];
    const page = (name: string) => ({ name, cleanup: () => (cleaned.push(name), true) });
    const a = page('a');
    const b = page('b');
    const shown = new ShownPage();
    shown.show(a);
    expect(cleaned).toEqual([]);
    shown.show(a); // the same page again (a resize): kept
    expect(cleaned).toEqual([]);
    shown.show(b);
    expect(cleaned).toEqual(['a']);
    shown.release();
    expect(cleaned).toEqual(['a', 'b']);
    shown.release();
    expect(cleaned).toEqual(['a', 'b']);
  });
});
