import type { ApiError } from '../api/client';

const TITLES: Record<number, string> = {
  0: 'No connection',
  400: 'Refused',
  403: 'Not allowed',
  404: 'Not found',
  409: 'Not possible now',
  415: 'Unsupported request',
  416: 'Range not satisfiable',
  422: 'Invalid input',
};

/** An API error: the HTTP status, the server's message, and a retry when one makes sense. */
export function ErrorBanner({ error, onRetry, onDismiss }: { error: ApiError; onRetry?: () => void; onDismiss?: () => void }) {
  const title = TITLES[error.status] ?? (error.status >= 500 ? 'Server error' : 'Request failed');
  return (
    <div className="banner banner-error" role="alert" data-testid="error-banner">
      <strong>
        {title}
        {error.status ? ` (${error.status})` : ''}
      </strong>
      <span className="banner-msg">{error.detail}</span>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Retry
        </button>
      )}
      {onDismiss && (
        <button type="button" className="btn" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}
