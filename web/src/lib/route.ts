import { useEffect, useState } from 'react';

// Hash routes: #/jobs and #/job/:id/{labels,workflow,progress,review,deliver}.
export const TABS = ['labels', 'workflow', 'progress', 'review', 'deliver'] as const;
export type Tab = (typeof TABS)[number];

export type Route = { name: 'jobs' } | { name: 'job'; jobId: string; tab: Tab };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean);
  if (parts[0] === 'job' && parts[1]) {
    const tab = (TABS as readonly string[]).includes(parts[2] ?? '') ? (parts[2] as Tab) : 'labels';
    return { name: 'job', jobId: decodeURIComponent(parts[1]), tab };
  }
  return { name: 'jobs' };
}

export const jobHref = (jobId: string, tab: Tab = 'labels') => `#/job/${encodeURIComponent(jobId)}/${tab}`;

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}

/** The page that fits a job's state: where opening a job from the list lands. */
export function tabForStatus(status: string): Tab {
  switch (status) {
    case 'approved':
    case 'extracted':
      return 'progress';
    case 'in_review':
      return 'review';
    case 'confirmed':
    case 'delivered':
      return 'deliver';
    default:
      return 'labels';
  }
}
