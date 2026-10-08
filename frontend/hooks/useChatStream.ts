"use client";

import { useState } from "react";
import { openEventStream, describeEvent, PlanStep, LiveEvent } from "@/lib/events";
import { getSession, SessionResponse } from "@/lib/api";
import { ChatMessage, newId, isGatewayTimeoutLikeError } from "@/components/chat/ChatBubble";

export function useChatStream(
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  onTurnEvent?: (event: LiveEvent) => void
) {
  const [narration, setNarration] = useState<string[]>([]);
  const [liveThinking, setLiveThinking] = useState("");

  async function withNarration(
    sid: string,
    fn: () => Promise<SessionResponse>,
  ): Promise<{ result: SessionResponse; thinking: string; seconds: number }> {
    setNarration([]);
    setLiveThinking("");
    const startedAt = Date.now();
    let thinking = "";

    return new Promise<{ result: SessionResponse; thinking: string; seconds: number }>((resolve, reject) => {
      let isDone = false;
      const cleanup = () => {
        if (isDone) return;
        isDone = true;
        close();
        setNarration([]);
        setLiveThinking("");
      };

      const close = openEventStream(sid, async (event) => {
        const line = describeEvent(event);
        if (line) setNarration((n) => [...n, line]);
        if (event.type === "llm_delta" && typeof event.text === "string") {
          thinking += event.text;
          setLiveThinking(thinking);
        }
        if (event.type === "plan_proposed" && Array.isArray(event.plan)) {
          setMessages((m) => [...m, { id: newId(), role: "plan", text: "", planSteps: event.plan as PlanStep[] }]);
        }
        onTurnEvent?.(event);

        if (event.type === "turn_completed") {
          try {
            const finalSession = await getSession(sid);
            cleanup();
            resolve({ result: finalSession, thinking, seconds: Math.round((Date.now() - startedAt) / 1000) });
          } catch (e) {
            cleanup();
            reject(e);
          }
        }
      });

      fn().catch((err) => {
        if (!isGatewayTimeoutLikeError(err)) {
          cleanup();
          reject(err);
        }
      });
    });
  }

  return {
    narration,
    setNarration,
    liveThinking,
    withNarration,
  };
}
