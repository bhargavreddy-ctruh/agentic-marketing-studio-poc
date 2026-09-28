"use client";

import { useMemo, useState } from "react";
import { LiveEvent, PipelineNode, buildPipelineNodes } from "@/lib/events";

export interface SpecialistAgent {
  id: string;
  name: string;
  shortName: string;
  role: string;
  avatar: string;
  badgeColor: string;
  glowColor: string;
  keywords: string[];
}

export const SPECIALIST_ROSTER: SpecialistAgent[] = [
  {
    id: "orchestrator",
    name: "Creative Director",
    shortName: "Director",
    role: "Orchestration & Strategy",
    avatar: "🎬",
    badgeColor: "bg-purple-500/20 text-purple-300 border-purple-500/30",
    glowColor: "rgba(168, 85, 247, 0.4)",
    keywords: ["orchestrator", "ideation", "classifier", "turn_started", "route_decided"],
  },
  {
    id: "copywriter",
    name: "Narrative Copywriter",
    shortName: "Writer",
    role: "Story & Ad Script",
    avatar: "✍️",
    badgeColor: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    glowColor: "rgba(245, 158, 11, 0.4)",
    keywords: ["copywriter", "narrative", "script", "scriptwriter"],
  },
  {
    id: "illustrator",
    name: "Art Specialist",
    shortName: "Art",
    role: "Visuals & Keyframes",
    avatar: "🎨",
    badgeColor: "bg-pink-500/20 text-pink-300 border-pink-500/30",
    glowColor: "rgba(236, 72, 153, 0.4)",
    keywords: ["illustrator", "visual_design_lead", "composition_artist", "lighting_designer", "image"],
  },
  {
    id: "motion",
    name: "Motion Engine",
    shortName: "Motion",
    role: "Cinematography & Video",
    avatar: "🎥",
    badgeColor: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    glowColor: "rgba(59, 130, 246, 0.4)",
    keywords: ["motion_lead", "camera_director", "shot_planner", "video"],
  },
  {
    id: "sound",
    name: "Sound Specialist",
    shortName: "Sound",
    role: "Kokoro TTS & Audio",
    avatar: "🎙️",
    badgeColor: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
    glowColor: "rgba(16, 185, 129, 0.4)",
    keywords: ["sound_designer", "audio", "voice", "kokoro"],
  },
  {
    id: "guardrails",
    name: "Brand Guard",
    shortName: "Guard",
    role: "Guardrails & QA",
    avatar: "🛡️",
    badgeColor: "bg-rose-500/20 text-rose-300 border-rose-500/30",
    glowColor: "rgba(244, 63, 94, 0.4)",
    keywords: ["compliance", "guardrail", "review", "brand_dna", "qa"],
  },
];

/** Shared match rule for both `getActiveSpecialist` and `agentStates` below — a node belongs to a
 * specialist when any of the specialist's keywords appears as a substring of the given text. */
function matchesSpecialist(text: string, agent: SpecialistAgent): boolean {
  const lower = text.toLowerCase();
  return agent.keywords.some((kw) => lower.includes(kw));
}

export function getActiveSpecialist(
  events: LiveEvent[],
  generating: boolean,
  generatingKind?: "image" | "video" | "audio" | "text" | null,
): { agent: SpecialistAgent; taskLabel: string; thinkingSnippet?: string } | null {
  if (!generating && events.length === 0) return null;

  const nodes = buildPipelineNodes(events);
  
  // 1. Look for currently running node
  const runningNode = nodes.slice().reverse().find((n) => n.status === "running");
  if (runningNode) {
    const rawLabel = runningNode.label || runningNode.id;
    const matched = SPECIALIST_ROSTER.find((spec) => matchesSpecialist(rawLabel, spec))
      || SPECIALIST_ROSTER[0];

    const thinkingClean = runningNode.thinking ? runningNode.thinking.trim().slice(-140) : undefined;
    return {
      agent: matched,
      taskLabel: `Working on ${runningNode.label}`,
      thinkingSnippet: thinkingClean,
    };
  }

  // 2. If generating flag is set without explicit running node, map by kind
  if (generating) {
    if (generatingKind === "video") {
      return { agent: SPECIALIST_ROSTER[3], taskLabel: "Rendering video sequence…" };
    }
    if (generatingKind === "audio") {
      return { agent: SPECIALIST_ROSTER[4], taskLabel: "Synthesizing voiceover…" };
    }
    if (generatingKind === "image") {
      return { agent: SPECIALIST_ROSTER[2], taskLabel: "Painting scene frame…" };
    }
    return { agent: SPECIALIST_ROSTER[0], taskLabel: "Orchestrating workflow…" };
  }

  return null;
}

interface AgentHUDProps {
  events: LiveEvent[];
  generating: boolean;
  generatingKind?: "image" | "video" | "audio" | "text" | null;
  className?: string;
}

export default function AgentHUD({
  events,
  generating,
  generatingKind,
  className = "",
}: AgentHUDProps) {
  const [hoveredAgentId, setHoveredAgentId] = useState<string | null>(null);

  const nodes = useMemo(() => buildPipelineNodes(events), [events]);

  const agentStates = useMemo(() => {
    return SPECIALIST_ROSTER.map((agent) => {
      // Find matching nodes for this agent
      const matchedNodes = nodes.filter((n) => matchesSpecialist(`${n.id} ${n.label}`, agent));

      const isRunning = matchedNodes.some((n) => n.status === "running") ||
        (generating && (
          (generatingKind === "image" && agent.id === "illustrator") ||
          (generatingKind === "video" && agent.id === "motion") ||
          (generatingKind === "audio" && agent.id === "sound") ||
          (!generatingKind && agent.id === "orchestrator")
        ));

      const isFailed = matchedNodes.some((n) => n.status === "failed");
      const isCompleted = matchedNodes.some((n) => n.status === "completed");

      const latestNode = matchedNodes.slice(-1)[0];
      const thoughtSnippet = latestNode?.thinking ? latestNode.thinking.trim().slice(-160) : undefined;
      const outputSnippet = latestNode?.output;

      let status: "active" | "completed" | "failed" | "idle" = "idle";
      if (isRunning) status = "active";
      else if (isFailed) status = "failed";
      else if (isCompleted) status = "completed";

      return {
        agent,
        status,
        thoughtSnippet,
        outputSnippet,
        toolsCount: latestNode?.tools?.length ?? 0,
      };
    });
  }, [nodes, generating, generatingKind]);

  const activeAgentInfo = useMemo(() => {
    return agentStates.find((s) => s.status === "active");
  }, [agentStates]);

  const hoveredState = useMemo(() => {
    return agentStates.find((s) => s.agent.id === hoveredAgentId);
  }, [agentStates, hoveredAgentId]);

  return (
    <div className={`relative pointer-events-auto flex items-center ${className}`}>
      {/* Outer Pill Container */}
      <div className="flex items-center gap-0.5 sm:gap-1 rounded-full border border-surface-700/60 bg-surface-900/70 p-0.5 sm:p-1 backdrop-blur-xl shadow-2xl transition-all duration-300 hover:border-surface-600/80">
        
        {/* Living Autonomous Indicator */}
        <div className="flex items-center gap-1.5 px-2 sm:px-2.5 py-1 text-xs font-medium text-surface-400 border-r border-surface-700/50 shrink-0">
          <span className="relative flex h-2 w-2">
            {generating ? (
              <>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-500" />
              </>
            ) : (
              <span className="inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            )}
          </span>
          <span className="text-[10px] font-mono uppercase tracking-wider text-surface-400 font-semibold whitespace-nowrap">
            {generating ? "Running" : "Ready"}
          </span>
        </div>

        {/* Specialist Agent Roster with visible names */}
        <div className="flex items-center gap-0.5 sm:gap-1 px-0.5">
          {agentStates.map(({ agent, status }) => {
            const isActive = status === "active";
            const isCompleted = status === "completed";
            const isFailed = status === "failed";

            return (
              <button
                key={agent.id}
                onMouseEnter={() => setHoveredAgentId(agent.id)}
                onMouseLeave={() => setHoveredAgentId(null)}
                className={`relative flex items-center gap-1 sm:gap-1.5 rounded-full px-2 py-0.5 sm:px-2.5 sm:py-1 text-xs font-medium transition-all duration-200 ${
                  isActive
                    ? "bg-brand-500/15 text-brand-600 dark:text-brand-300 shadow-md ring-1 ring-brand-500/60 scale-105"
                    : isCompleted
                    ? "text-surface-200 hover:bg-surface-800/80 hover:text-surface-50"
                    : isFailed
                    ? "text-red-500 hover:bg-red-500/10"
                    : "text-surface-400 hover:bg-surface-800/60 hover:text-surface-200"
                }`}
                style={
                  isActive
                    ? {
                        boxShadow: `0 0 16px ${agent.glowColor}`,
                        borderColor: "rgba(99, 102, 241, 0.5)",
                      }
                    : undefined
                }
                title={`${agent.name} (${status}) — ${agent.role}`}
              >
                <span className="text-xs sm:text-sm">{agent.avatar}</span>
                {/* Clear, readable name for each agent */}
                <span className="text-[11px] font-medium whitespace-nowrap">
                  {agent.shortName}
                </span>

                {/* Status Dot */}
                <span className="relative flex h-1.5 w-1.5 shrink-0">
                  {isActive ? (
                    <>
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-brand-500" />
                    </>
                  ) : isCompleted ? (
                    <span className="inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  ) : isFailed ? (
                    <span className="inline-flex h-1.5 w-1.5 rounded-full bg-red-500" />
                  ) : (
                    <span className="inline-flex h-1.5 w-1.5 rounded-full bg-surface-500/50" />
                  )}
                </span>
              </button>
            );
          })}
        </div>

        {/* Live Active Thought Mini-Marquee (when generating) */}
        {activeAgentInfo && (
          <div className="hidden lg:flex items-center gap-2 pl-2 pr-3 py-0.5 border-l border-surface-700/50 text-xs text-brand-600 dark:text-brand-300 max-w-xs truncate animate-fade-in">
            <span className="text-[11px] font-mono text-brand-500 font-semibold">
              {activeAgentInfo.agent.shortName}:
            </span>
            <span className="truncate text-surface-300 font-light italic">
              {activeAgentInfo.thoughtSnippet || "Processing..."}
            </span>
          </div>
        )}

      </div>

      {/* Hover Info Card / Tooltip */}
      {hoveredState && (
        <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-64 rounded-2xl border border-surface-700/60 bg-surface-900/95 p-3 shadow-2xl backdrop-blur-2xl z-50 animate-fade-in-up text-left">
          <div className="flex items-center justify-between gap-2 border-b border-surface-800 pb-2 mb-2">
            <div className="flex items-center gap-2">
              <span className="text-xl">{hoveredState.agent.avatar}</span>
              <div>
                <p className="text-xs font-semibold text-white">{hoveredState.agent.name}</p>
                <p className="text-[10px] text-surface-400">{hoveredState.agent.role}</p>
              </div>
            </div>
            <span
              className={`text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full ${
                hoveredState.status === "active"
                  ? "bg-brand-500/20 text-brand-300 border border-brand-500/30 animate-pulse"
                  : hoveredState.status === "completed"
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                  : hoveredState.status === "failed"
                  ? "bg-red-500/20 text-red-300 border border-red-500/30"
                  : "bg-surface-800 text-surface-400 border border-surface-700/50"
              }`}
            >
              {hoveredState.status}
            </span>
          </div>

          {hoveredState.thoughtSnippet ? (
            <div className="space-y-1">
              <p className="text-[10px] font-mono text-surface-500 uppercase">Latest Streamed Thought:</p>
              <p className="rounded-lg bg-surface-950/60 p-2 font-mono text-[10px] text-surface-300 leading-relaxed max-h-24 overflow-y-auto whitespace-pre-wrap">
                {hoveredState.thoughtSnippet}
              </p>
            </div>
          ) : hoveredState.outputSnippet ? (
            <div className="space-y-1">
              <p className="text-[10px] font-mono text-surface-500 uppercase">Outcome:</p>
              <p className="text-xs text-surface-300">{hoveredState.outputSnippet}</p>
            </div>
          ) : (
            <p className="text-[11px] text-surface-500 italic">
              Awaiting trigger in current workflow turn.
            </p>
          )}

          {hoveredState.toolsCount > 0 && (
            <div className="mt-2 pt-2 border-t border-surface-800 flex items-center justify-between text-[10px] text-surface-400">
              <span>Tools executed:</span>
              <span className="font-mono text-surface-200">{hoveredState.toolsCount}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
