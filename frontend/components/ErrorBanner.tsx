/**
 * Shared error banner — unified dark-theme error display used across all pages/components.
 * Replaces the inconsistent mix of `text-red-700` (light), `bg-red-900/80 text-red-200` (canvas),
 * and `text-sm text-red-700` (settings forms).
 */
export function ErrorBanner({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-red-700/40 bg-red-900/20 px-4 py-2.5 text-sm text-red-300">
      <span>{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 text-red-400 transition-colors hover:text-red-200"
          aria-label="Dismiss error"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}
