import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { useEffect, useState } from 'react';
import { sourcePdfUrl } from '../api/client';

// Everything pdf.js fetches comes from this server: the worker is a build asset, the CMaps, standard fonts and
// decoders are copied to static/pdfjs/ (scripts/copy-pdfjs-assets.mjs).
GlobalWorkerOptions.workerSrc = workerUrl;
const ASSETS = `${import.meta.env.BASE_URL}pdfjs/`;

export interface PdfState {
  doc: PDFDocumentProxy | null;
  error: string | null;
}

/** The job's source.pdf, loaded once per job by HTTP Range (64 KiB chunks, no pre-fetch); destroyed (with its
 * worker) when the job changes or the viewer goes away. */
export function usePdfDocument(jobId: string): PdfState {
  const [state, setState] = useState<PdfState>({ doc: null, error: null });
  useEffect(() => {
    setState({ doc: null, error: null });
    const task = getDocument({
      url: sourcePdfUrl(jobId),
      rangeChunkSize: 65536,
      disableAutoFetch: true,
      disableStream: true,
      cMapUrl: `${ASSETS}cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${ASSETS}standard_fonts/`,
      wasmUrl: `${ASSETS}wasm/`,
      iccUrl: `${ASSETS}iccs/`,
      useSystemFonts: false,
      enableXfa: false,
    });
    let live = true;
    task.promise.then(
      (doc) => live && setState({ doc, error: null }),
      (e: unknown) => live && setState({ doc: null, error: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
      void task.destroy();
    };
  }, [jobId]);
  return state;
}
