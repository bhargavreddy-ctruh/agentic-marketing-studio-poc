/**
 * Shared close button used by all modals — extracted so every modal (StyleLockModal,
 * AdSpecExportModal, CanvasMaskEditorModal, and the inline Modal wrapper in page.tsx) uses the
 * same visual treatment rather than each keeping its own copy of the "✕" text button or inline SVG.
 * Zero behavioral change — it's a presentational wrapper around a single onClick.
 */
export function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      onClick={onClose}
      className="absolute right-4 top-4 z-10 rounded-full p-1.5 text-surface-400 transition-colors hover:bg-surface-800 hover:text-white"
      aria-label="Close"
    >
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
      </svg>
    </button>
  );
}
