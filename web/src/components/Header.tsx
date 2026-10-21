import { useLayoutEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { JobDetail } from '../api/client';

function pagesOf(detail: JobDetail | null): number | null {
  const pages = detail?.stats?.pages;
  return typeof pages === 'number' ? pages : null;
}

function langOf(detail: JobDetail | null): string | null {
  const lang = detail?.job.declaration?.lang;
  return typeof lang === 'string' && lang ? lang : null;
}

/** The top bar: brand, the job's file (pages, language), a slot (search) and the job id. */
export function Header({ detail, children }: { detail?: JobDetail | null; children?: ReactNode }) {
  const pages = pagesOf(detail ?? null);
  const lang = langOf(detail ?? null);
  const facts = [pages !== null ? `${pages} page${pages === 1 ? '' : 's'}` : null, lang].filter(Boolean).join(' · ');
  return (
    <header className="hdr">
      <a className="hdr-brand" href="#/jobs">
        kb-intake
      </a>
      {detail && (
        <div className="hdr-file" data-testid="header-file">
          <span className="mono">{detail.job.source_name}</span>
          {facts && <span className="muted">{facts}</span>}
        </div>
      )}
      <div className="hdr-slot" data-header-slot="">
        {children}
      </div>
      {detail && <div className="hdr-job">Job {detail.job.id}</div>}
    </header>
  );
}

/** Puts its children into the header's slot (the search box of a page); renders nothing until the header is there. */
export function HeaderSlot({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<Element | null>(null);
  useLayoutEffect(() => setSlot(document.querySelector('[data-header-slot]')), []);
  return slot ? createPortal(children, slot) : null;
}
