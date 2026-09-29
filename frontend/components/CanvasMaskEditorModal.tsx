"use client";

import { useEffect, useRef, useState } from "react";
import { request } from "@/lib/http";
import { CloseButton } from "@/components/CloseButton";

interface CanvasMaskEditorModalProps {
  elementId: string | null;
  storageRef: string | null;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export default function CanvasMaskEditorModal({
  elementId,
  storageRef,
  isOpen,
  onClose,
  onSuccess,
}: CanvasMaskEditorModalProps) {
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [brushSize, setBrushSize] = useState(30);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const isDrawingRef = useRef(false);

  useEffect(() => {
    if (isOpen && canvasRef.current) {
      const ctx = canvasRef.current.getContext("2d");
      if (ctx) {
        ctx.fillStyle = "black";
        ctx.fillRect(0, 0, 512, 512);
      }
    }
  }, [isOpen]);

  // Esc key closes the modal (8e).
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen || !elementId || !storageRef) return null;

  const drawMask = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawingRef.current || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;

    const rect = canvasRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 512;
    const y = ((e.clientY - rect.top) / rect.height) * 512;

    ctx.fillStyle = "white";
    ctx.beginPath();
    ctx.arc(x, y, brushSize / 2, 0, Math.PI * 2);
    ctx.fill();
  };

  const handleClearMask = () => {
    if (!canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "black";
      ctx.fillRect(0, 0, 512, 512);
    }
  };

  const handleSubmit = async () => {
    if (!instruction.trim() || !canvasRef.current) return;
    setSubmitting(true);
    try {
      // 1. Export canvas mask to Blob
      const blob = await new Promise<Blob | null>((resolve) =>
        canvasRef.current?.toBlob(resolve, "image/png")
      );
      if (!blob) throw new Error("Failed to export mask canvas");

      // 2. Upload mask asset
      const formData = new FormData();
      formData.append("file", blob, "mask.png");
      const uploadRes = await fetch("/api/v1/canvas/assets", { method: "POST", body: formData });
      const { storage_ref: maskStorageRef } = await uploadRes.json();

      // 3. Post masked edit
      await request(`/api/v1/canvas/elements/${elementId}/masked-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction,
          mask_storage_ref: maskStorageRef,
        }),
      });

      onSuccess();
      onClose();
    } catch {
      alert("Masked edit failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-xl rounded-2xl border border-surface-700 bg-surface-900 p-6 shadow-2xl">
        <CloseButton onClose={onClose} />
        <div className="flex items-center border-b border-surface-800 pb-3 mb-4">
          <h3 className="text-base font-semibold text-surface-50">Brush-Mask Inpainting Editor</h3>
        </div>

        <div className="space-y-4">
          <div className="relative mx-auto aspect-square w-full max-w-[380px] overflow-hidden rounded-xl border border-surface-700 bg-black">
            <img
              src={`/api/v1/canvas/assets/${storageRef}`}
              alt="Source"
              className="absolute inset-0 h-full w-full object-contain pointer-events-none opacity-60"
            />
            <canvas
              ref={canvasRef}
              width={512}
              height={512}
              onMouseDown={(e) => {
                isDrawingRef.current = true;
                drawMask(e);
              }}
              onMouseMove={drawMask}
              onMouseUp={() => (isDrawingRef.current = false)}
              onMouseLeave={() => (isDrawingRef.current = false)}
              className="absolute inset-0 h-full w-full cursor-crosshair opacity-80 mix-blend-screen"
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <span className="text-xs text-surface-300">Brush Size:</span>
              <input
                type="range"
                min="10"
                max="80"
                value={brushSize}
                onChange={(e) => setBrushSize(Number(e.target.value))}
                className="w-24 accent-brand-500"
              />
            </div>
            <button
              onClick={handleClearMask}
              className="rounded-lg bg-surface-800 px-3 py-1 text-xs text-surface-300 hover:bg-surface-700"
            >
              Clear Mask
            </button>
          </div>

          <div>
            <label className="block text-xs font-medium text-surface-300 mb-1">Edit Instruction</label>
            <input
              type="text"
              placeholder="e.g. Change the highlighted sneakers to bright red leather"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              className="w-full rounded-xl border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-surface-50 focus:border-brand-500 focus:outline-none"
            />
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3 pt-3 border-t border-surface-800">
          <button onClick={onClose} className="rounded-lg px-4 py-2 text-xs text-surface-300 hover:bg-surface-800">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !instruction.trim()}
            className="rounded-lg bg-brand-500 px-5 py-2 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {submitting ? "Inpainting..." : "Apply Masked Edit"}
          </button>
        </div>
      </div>
    </div>
  );
}
