import { Busy } from './components/Busy';
import { ErrorBanner } from './components/ErrorBanner';
import { Header } from './components/Header';
import { StepBar } from './components/StepBar';
import { HealthContext, useHealthOnce, useJob } from './lib/hooks';
import { useRoute, type Tab } from './lib/route';
import { JobsPage } from './pages/Jobs';
import { LabelsPage } from './pages/Labels';
import { Placeholder } from './pages/Placeholder';

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
      {detail && (tab === 'labels' ? <LabelsPage detail={detail} /> : <Placeholder title={TITLES[tab]} />)}
    </div>
  );
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
