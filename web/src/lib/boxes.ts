// Boxes are PDF points with the origin at the top left of the page (docs/api-v1.md); the overlay places them as
// percentages of the page, so they follow the rendered page at any size.
export type Box = [number, number, number, number];

export interface CssBox {
  left: string;
  top: string;
  width: string;
  height: string;
}

const pct = (v: number) => `${(Math.round(v * 10000) / 100).toString()}%`;

export function toCss(box: Box, pageWidth: number, pageHeight: number): CssBox {
  const [x0, y0, x1, y1] = box;
  return {
    left: pct(x0 / pageWidth),
    top: pct(y0 / pageHeight),
    width: pct(Math.max(0, x1 - x0) / pageWidth),
    height: pct(Math.max(0, y1 - y0) / pageHeight),
  };
}

/** A [x0, y0, x1, y1] box of four finite numbers, or null (for fields typed `unknown`). */
export function asBox(v: unknown): Box | null {
  if (!Array.isArray(v) || v.length !== 4 || !v.every((n) => typeof n === 'number' && Number.isFinite(n))) return null;
  return v as Box;
}
