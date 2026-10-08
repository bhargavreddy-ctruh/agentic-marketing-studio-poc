"use client";

import { RefObject } from "react";

interface ChatInputProps {
  input: string;
  setInput: (val: string) => void;
  inputRef: RefObject<HTMLTextAreaElement>;
  fileInputRef: RefObject<HTMLInputElement>;
  loading: boolean;
  attaching: boolean;
  sessionId: string | null;
  handleSend: () => void;
  handlePaste: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  handleDrop: (e: React.DragEvent<HTMLTextAreaElement>) => void;
  attachFile: (file: File) => void;
  showLinkPopover: boolean;
  setShowLinkPopover: (val: boolean | ((v: boolean) => boolean)) => void;
  linkInput: string;
  setLinkInput: (val: string) => void;
  linkCrawling: boolean;
  handleTriggerLinkCrawl: () => void;
  handleCancelTurn: () => void;
}

export function ChatInput({
  input,
  setInput,
  inputRef,
  fileInputRef,
  loading,
  attaching,
  sessionId,
  handleSend,
  handlePaste,
  handleDrop,
  attachFile,
  showLinkPopover,
  setShowLinkPopover,
  linkInput,
  setLinkInput,
  linkCrawling,
  handleTriggerLinkCrawl,
  handleCancelTurn,
}: ChatInputProps) {
  return (
    <form
      className="relative mt-4 flex flex-col gap-2 rounded-2xl glass-input p-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        handleSend();
      }}
    >
      {showLinkPopover && (
        <div className="absolute bottom-full left-0 right-0 z-10 mb-2 rounded-xl border border-surface-700 bg-surface-900 p-2.5 shadow-xl">
          <p className="mb-1.5 text-[10px] font-semibold text-surface-400">Paste a company or product link</p>
          <div className="flex gap-1.5">
            <input
              autoFocus
              className="min-w-0 flex-1 rounded-lg border border-surface-700 bg-surface-800 px-2 py-1.5 text-xs text-surface-50 focus:border-brand-500 focus:outline-none"
              placeholder="https://..."
              value={linkInput}
              onChange={(e) => setLinkInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleTriggerLinkCrawl()}
            />
            <button
              type="button"
              onClick={handleTriggerLinkCrawl}
              disabled={linkCrawling || !linkInput.trim()}
              className="shrink-0 rounded-lg bg-brand-500 px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
            >
              {linkCrawling ? "…" : "Crawl"}
            </button>
          </div>
        </div>
      )}
      <textarea
        ref={inputRef}
        rows={1}
        className="max-h-32 w-full resize-none bg-transparent px-3 py-2 text-sm text-surface-50 placeholder-surface-400 focus:outline-none scrollbar-thin scrollbar-track-transparent scrollbar-thumb-surface-700"
        placeholder={sessionId ? "What do you want to do?" : "What do you want to create?"}
        value={input}
        onChange={(e) => {
          setInput(e.target.value);
          const el = e.target;
          el.style.height = "auto";
          el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
        onPaste={handlePaste}
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        disabled={loading}
      />
      <div className="flex items-center justify-between px-2 pb-1">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setShowLinkPopover((v) => !v)}
            disabled={!sessionId}
            className="text-surface-500 hover:text-surface-300 transition-colors disabled:opacity-30"
            title="Add a brand/product link to auto-extract DNA"
          >
            🔗
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void attachFile(file);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={!sessionId || attaching}
            className="text-surface-500 hover:text-surface-300 transition-colors disabled:opacity-30"
            title="Attach an image (or paste/drop one into the message box)"
          >
            {attaching ? "…" : "📎"}
          </button>
          <button type="button" className="text-surface-500 hover:text-surface-300 transition-colors" title="Voice Input">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </button>
          {loading ? (
            <button
              type="button"
              onClick={handleCancelTurn}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-200 text-surface-900 transition-transform hover:scale-105 shadow-sm"
            >
              <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-500 text-white transition-transform disabled:opacity-30 disabled:hover:scale-100 hover:scale-105 shadow-md shadow-brand-500/25"
            >
              <svg className="h-4 w-4 translate-x-[1px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
