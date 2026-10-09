"use client";

import { RefObject, useEffect, useRef, useState } from "react";

export interface FormatOption {
  id: string;
  label: string;
  ratio: string;
  icon: string;
  description: string;
  promptSuffix?: string;
}

export const FORMAT_OPTIONS: FormatOption[] = [
  {
    id: "auto",
    label: "Auto (Smart AI)",
    ratio: "Auto",
    icon: "⚡",
    description: "AI decides the optimal deliverable and ratio",
  },
  {
    id: "square",
    label: "Square Post (1:1)",
    ratio: "1:1",
    icon: "⏹️",
    description: "Universal square post / product visual (1080x1080)",
    promptSuffix: "deliverable format: 1:1 square visual",
  },
  {
    id: "portrait",
    label: "Vertical Portrait (4:5)",
    ratio: "4:5",
    icon: "📱",
    description: "Vertical high-impact feed visual (1080x1350)",
    promptSuffix: "deliverable format: 4:5 vertical portrait",
  },
  {
    id: "vertical_story",
    label: "Full Screen Story (9:16)",
    ratio: "9:16",
    icon: "🎬",
    description: "Full vertical mobile / story / reel (1080x1920)",
    promptSuffix: "deliverable format: 9:16 vertical story/reel",
  },
  {
    id: "landscape_banner",
    label: "Hero Banner (16:9)",
    ratio: "16:9",
    icon: "🖥️",
    description: "Landscape hero banner / e-commerce header (1920x1080)",
    promptSuffix: "deliverable format: 16:9 landscape hero banner",
  },
  {
    id: "campaign_package",
    label: "Full Campaign Suite",
    ratio: "Campaign",
    icon: "🚀",
    description: "Multi-deliverable campaign with visuals, badges, and copy",
    promptSuffix: "full marketing campaign with hero visuals, promotional badges, and marketing copy",
  },
];

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
  selectedFormat?: string;
  setSelectedFormat?: (val: string) => void;
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
  selectedFormat = "auto",
  setSelectedFormat,
}: ChatInputProps) {
  const [showFormatMenu, setShowFormatMenu] = useState(false);
  const formatMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (formatMenuRef.current && !formatMenuRef.current.contains(event.target as Node)) {
        setShowFormatMenu(false);
      }
    }
    if (showFormatMenu) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [showFormatMenu]);

  const activeFormat = FORMAT_OPTIONS.find((f) => f.id === selectedFormat) || FORMAT_OPTIONS[0];
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
        <div className="flex items-center gap-2">
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

          {/* Format / Aspect Ratio Selector Pill */}
          <div className="relative" ref={formatMenuRef}>
            <button
              type="button"
              onClick={() => setShowFormatMenu((v) => !v)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-all ${
                selectedFormat !== "auto"
                  ? "bg-brand-500/20 text-brand-300 border border-brand-500/40 shadow-sm"
                  : "bg-surface-800/80 text-surface-400 hover:text-surface-200 hover:bg-surface-800 border border-surface-700/60"
              }`}
              title="Target aspect ratio / platform format"
            >
              <span>{activeFormat.icon}</span>
              <span className="font-medium tracking-tight">{activeFormat.ratio}</span>
              <svg
                className={`w-3 h-3 opacity-60 transition-transform ${showFormatMenu ? "rotate-180" : ""}`}
                viewBox="0 0 20 20"
                fill="currentColor"
              >
                <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </button>

            {showFormatMenu && (
              <div className="absolute bottom-full left-0 z-30 mb-2 w-72 rounded-xl border border-surface-700 bg-surface-900/95 p-1.5 shadow-2xl backdrop-blur-md">
                <div className="px-2 py-1 text-[10px] font-semibold tracking-wider text-surface-400 uppercase">
                  Deliverable Format / Ratio
                </div>
                <div className="flex flex-col gap-0.5">
                  {FORMAT_OPTIONS.map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => {
                        setSelectedFormat?.(opt.id);
                        setShowFormatMenu(false);
                      }}
                      className={`flex items-start gap-2.5 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                        selectedFormat === opt.id
                          ? "bg-brand-500/20 text-brand-200"
                          : "text-surface-300 hover:bg-surface-800 hover:text-surface-100"
                      }`}
                    >
                      <span className="text-sm mt-0.5">{opt.icon}</span>
                      <div className="flex flex-col flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <span className="font-medium truncate">{opt.label}</span>
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-surface-800 text-surface-400 font-mono">
                            {opt.ratio}
                          </span>
                        </div>
                        <span className="text-[10px] text-surface-400 leading-tight">
                          {opt.description}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
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
