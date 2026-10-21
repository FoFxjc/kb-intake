import type { JobDetail } from './api/client';
import { Busy } from './components/Busy';
import { ErrorBanner } from './components/ErrorBanner';
import { Header } from './components/Header';
import { StepBar } from './components/StepBar';
import { HealthContext, useHealthOnce, useJob } from './lib/hooks';
import { useRoute, type Tab } from './lib/route';
import { DeliverPage } from './pages/Deliver';
import { JobsPage } from './pages/Jobs';
import { LabelsPage } from './pages/Labels';
import { Placeholder } from './pages/Placeholder';
import { ProgressPage } from './pages/Progress';
import { WorkflowPage } from './pages/Workflow';

const TITLES: Record<Tab, string> = {
  labels: 'Labels & inventory',
  workflow: 'Workflow & approval',
  progress: 'Extraction progress',
  review: 'Review inbox',
  deliver: 'Confirm & deliver',
};

function JobShell({ jobId, tab }: { jobId: string; tab: Tab }) {
  const { detail, error, reload } = useJob(jobId);
  return (
    <div className="app">
      <Header detail={detail} />
      {detail && <StepBar job={detail.job} tab={tab} />}
      {(error || detail?.job.running_op || detail?.job.last_error) && (
        <div style={{ padding: '12px var(--pad-page) 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {error && <ErrorBanner error={error} onRetry={reload} />}
          {detail?.job.running_op && (
            <div>
              <Busy op={detail.job.running_op} />
            </div>
          )}
          {detail?.job.last_error && !detail.job.running_op && (
            <div className="banner banner-error" role="alert">
              <strong>The last step failed</strong>
              <span className="banner-msg">{detail.job.last_error}</span>
            </div>
          )}
        </div>
      )}
      {detail && <JobPage tab={tab} detail={detail} reload={reload} />}
    </div>
  );
}

function JobPage({ tab, detail, reload }: { tab: Tab; detail: JobDetail; reload: () => void }) {
  switch (tab) {
    case 'labels':
      return <LabelsPage detail={detail} />;
    case 'workflow':
      return <WorkflowPage detail={detail} onChanged={reload} />;
    case 'progress':
      return <ProgressPage detail={detail} onChanged={reload} />;
    case 'deliver':
      return <DeliverPage detail={detail} onChanged={reload} />;
    default:
      return <Placeholder title={TITLES[tab]} />;
  }
}

export function App() {
  const route = useRoute();
  const { health } = useHealthOnce();
  return (
    <HealthContext.Provider value={health}>
      {route.name === 'job' ? <JobShell key={route.jobId} jobId={route.jobId} tab={route.tab} /> : <JobsPage />}
    </HealthContext.Provider>
  );
}
