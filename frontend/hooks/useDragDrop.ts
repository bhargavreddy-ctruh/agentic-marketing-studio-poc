"use client";

import { useState } from "react";
import { CanvasElement, uploadAndPlaceElement } from "@/lib/canvas";
import { ReferencedElement, elementKind } from "@/components/CanvasView";
import { assetUrl } from "@/lib/http";

export function useDragDrop(
  sessionId: string | null,
  onAddReferenceElement?: (el: ReferencedElement) => void,
  onGenerated?: () => void
) {
  const [attaching, setAttaching] = useState(false);

  async function attachFile(file: File) {
    if (!sessionId || !file.type.startsWith("image/")) return;
    setAttaching(true);
    try {
      const el: CanvasElement = await uploadAndPlaceElement(sessionId, file);
      onAddReferenceElement?.({
        id: el.id,
        kind: elementKind(el.element_type),
        url: el.storage_ref ? assetUrl(el.storage_ref, el.url) : "",
        description: el.description,
        productId: el.product_id,
        productName: el.product_name,
      });
      onGenerated?.(); // bump the canvas refresh so the uploaded tile shows up there too
    } catch (err) {
      console.error("attachFile failed", err);
    } finally {
      setAttaching(false);
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const item = Array.from(e.clipboardData.items).find((i) => i.type.startsWith("image/"));
    if (!item) return;
    const file = item.getAsFile();
    if (file) {
      e.preventDefault();
      void attachFile(file);
    }
  }

  function handleDrop(e: React.DragEvent<HTMLTextAreaElement>) {
    const file = Array.from(e.dataTransfer.files).find((f) => f.type.startsWith("image/"));
    if (file) {
      e.preventDefault();
      void attachFile(file);
    }
  }

  return {
    attaching,
    attachFile,
    handlePaste,
    handleDrop,
  };
}
