/**
 * Shared loading spinner — replaces all inline SVG spinner duplicates across the codebase.
 * Uses brand-500 by default to match the studio's colour system.
 */
export function Spinner({ size = 5, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      className={`h-${size} w-${size} animate-spin text-brand-500 ${className}`}
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}
