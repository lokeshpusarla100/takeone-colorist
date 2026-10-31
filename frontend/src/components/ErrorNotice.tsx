import type { AppError } from '../lib/errors';
export default function ErrorNotice({ error, onDismiss, onRetry }: {
  error:AppError; onDismiss?:()=>void; onRetry?:()=>void;
}) {
  return <section className="error-notice" role="alert">
    <div><strong>{error.message}</strong>{onDismiss && <button aria-label="Dismiss error" onClick={onDismiss}>Dismiss</button>}</div>
    <p>{error.action}</p>
    <details><summary>Technical details</summary><pre>{JSON.stringify({code:error.code,...error.details},null,2)}</pre></details>
    {onRetry && <button onClick={onRetry}>Retry save</button>}
  </section>;
}
