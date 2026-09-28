"use client";

import { useEffect, useRef } from "react";
import { CloseButton } from "@/components/CloseButton";

/**
 * Inline replacement for `window.prompt()` — purely a visual swap.
 * Behavior is identical: collects a string and calls onConfirm(value), or calls onCancel.
 * Used everywhere the native browser dialog was called (CanvasView regenerate/comment,
 * studio page handleRequestGenerate) so the studio dark theme is never broken by a
 * system-native dialog box.
 */
export interface PromptModalProps {
  isOpen: boolean;
  label: string;
  placeholder?: string;
  /** Called with the trimmed value when the user submits the form. */
  onConfirm: (value: string) => void;
  /** Called when the user cancels (✕ button, Escape, or clicking outside). */
  onCancel: () => void;
}

export function PromptModal({
  isOpen,
  label,
  placeholder = "",
  onConfirm,
  onCancel,
}: PromptModalProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus the input when the modal opens so the user can type immediately.
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // Esc key closes / cancels — same pattern used by the Modal wrapper in page.tsx.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onCancel]);

  if (!isOpen) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const value = (inputRef.current?.value ?? "").trim();
    if (!value) return;
    onConfirm(value);
  }

  return (
    // Clicking the backdrop cancels — mirrors the existing Guardrails/DNA modal behaviour.
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onPointerDown={onCancel}
    >
      <div
        className="relative w-full max-w-md rounded-xl border border-surface-700 bg-surface-900 p-6 shadow-2xl"
        onPointerDown={(e) => e.stopPropagation()} // prevent backdrop click from firing inside the card
      >
        <CloseButton onClose={onCancel} />

        <form onSubmit={handleSubmit} className="mt-2 flex flex-col gap-4">
          <label className="block text-sm font-medium text-surface-200">{label}</label>
          <input
            ref={inputRef}
            type="text"
            placeholder={placeholder}
            className="w-full rounded-xl border border-surface-700 bg-surface-800 px-3 py-2.5 text-sm text-white placeholder:text-surface-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg px-4 py-2 text-xs text-surface-300 transition-colors hover:bg-surface-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded-lg bg-brand-500 px-5 py-2 text-xs font-medium text-white transition-colors hover:bg-brand-600"
            >
              Confirm
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
