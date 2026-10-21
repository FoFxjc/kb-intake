import { RenderingCancelledException, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist';
import { useEffect, useRef, useState, type ReactNode } from 'react';

interface Props {
  doc: PDFDocumentProxy | null;
  pageNo: number;
  /** page size in PDF points (from the page view), used until pdf.js has the page */
  width?: number;
  height?: number;
  /** drawn over the page (the overlay), sized to the page */
  children?: ReactNode;
  error?: string | null;
}

/** One PDF page on one canvas at the device pixel ratio. A page change cancels the render in flight before the
 * next one starts, so fast switching never draws a stale page or leaves a second canvas. */
export function PdfPage({ doc, pageNo, width, height, children, error }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const task = useRef<RenderTask | null>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [cssWidth, setCssWidth] = useState(0);
  const [failed, setFailed] = useState<string | null>(null);
  const [rendered, setRendered] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      setCssWidth((old) => (Math.abs(old - w) >= 2 ? w : old));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!doc || !cssWidth || !canvas.current) return;
    let stale = false;
    const target = canvas.current;
    setFailed(null);
    (async () => {
      const previous = task.current;
      if (previous) {
        previous.cancel();
        try {
          await previous.promise;
        } catch {
          // cancelled
        }
      }
      if (stale) return;
      const page = await doc.getPage(pageNo);
      if (stale) return;
      const base = page.getViewport({ scale: 1 });
      setSize({ w: base.width, h: base.height });
      const dpr = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: (cssWidth / base.width) * dpr });
      target.width = Math.floor(viewport.width);
      target.height = Math.floor(viewport.height);
      const t = page.render({ canvas: target, viewport });
      task.current = t;
      try {
        await t.promise;
        if (!stale) setRendered(pageNo);
      } finally {
        if (task.current === t) task.current = null;
      }
    })().catch((e: unknown) => {
      if (stale || e instanceof RenderingCancelledException) return;
      setFailed(e instanceof Error ? e.message : String(e));
    });
    return () => {
      stale = true;
      task.current?.cancel();
    };
  }, [doc, pageNo, cssWidth]);

  const w = size?.w ?? width ?? 595;
  const h = size?.h ?? height ?? 842;
  const message = error ?? failed ?? (!doc ? 'Loading the PDF…' : null);
  return (
    <div
      ref={box}
      className="pdfpage"
      style={{ aspectRatio: `${w} / ${h}` }}
      data-testid="pdf-page"
      data-page={pageNo}
      data-rendered={rendered === pageNo ? 'true' : 'false'}
    >
      <canvas ref={canvas} aria-label={`Page ${pageNo}`} role="img" />
      {children}
      {message && <div className="pdfpage-msg">{message}</div>}
    </div>
  );
}
