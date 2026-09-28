# Frontend Improvement Recommendations

> **Scope**: UI/UX improvements to the existing Next.js frontend only.  
> **Constraint**: No backend changes. All improvements work against the existing API contracts,
> SSE event shapes, and data models exactly as they are today.  
> **Priority order**: P1 = high visual/UX impact, easy to implement. P2 = medium effort, clear
> improvement. P3 = polish and delight.

---

## 1. Home Page (`app/page.tsx`)

### 1a — Unified dark theme on the home page [P1]

**Problem**: The root layout sets `className="dark"` and `bg-mesh-dark` globally, but the home
page overrides everything with `bg-white`, `border-neutral-200`, `bg-neutral-50` — creating a
jarring light-on-dark mismatch. The global body background bleeds through around the content area.

**Fix**: Rewrite the home page to use the same `surface` token scale the studio already uses.
Replace every `bg-white`, `bg-neutral-50`, `border-neutral-200`, `text-neutral-900`,
`hover:bg-neutral-100` with their dark equivalents:

| Current | Replace with |
|---|---|
| `bg-white` (cards, form panels) | `bg-surface-900 border border-surface-700/50` |
| `bg-neutral-50` (page body bg) | _(remove — body bg is already `bg-mesh-dark`)_ |
| `border-neutral-200` | `border-surface-700/50` |
| `text-neutral-900` (titles) | `text-surface-50` |
| `text-neutral-500` (subtitles, metas) | `text-surface-400` |
| `text-neutral-400` (empty states) | `text-surface-500` |
| `hover:bg-neutral-50` / `hover:bg-neutral-100` | `hover:bg-surface-800/60` |
| `rounded-2xl border border-neutral-200 bg-white shadow-sm` (Settings panels) | `rounded-2xl border border-surface-700/50 bg-surface-900/60 backdrop-blur-xl shadow-xl` |
| `bg-neutral-900 text-white` (primary buttons) | `bg-brand-500 text-white hover:bg-brand-600` |
| `border border-neutral-300` (inputs) | `border border-surface-700 bg-surface-800 focus:border-brand-500 focus:ring-1 focus:ring-brand-500` |

This brings the home page into the same visual language as the studio without any behavioral change.

---

### 1b — Workflow cards: status badges instead of raw text [P1]

**Problem**: Each workflow card shows `{w.status} · {w.approval_mode}` as raw lowercase text
(`"ideating · auto"`). This looks unfinished and wastes the status information.

**Fix**: Replace the plain text with styled pill badges that match the existing color convention
already used in `NodeGraphView` (emerald/red/blue for status states):

```tsx
// Status badge helper
function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed: "bg-emerald-900/40 text-emerald-400 border-emerald-700/40",
    generating: "bg-blue-900/40 text-blue-400 border-blue-700/40 animate-pulse-soft",
    error:      "bg-red-900/40 text-red-400 border-red-700/40",
    ideating:   "bg-surface-800 text-surface-400 border-surface-700/50",
    awaiting_approval: "bg-amber-900/40 text-amber-400 border-amber-700/40",
  };
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${map[status] ?? map.ideating}`}>
      {status.replace("_", " ")}
    </span>
  );
}
```

Add a separate `ApprovalBadge` pill (`auto` → gray, `approve` → amber) alongside it.

---

### 1c — Settings panels: replace toggleable section with a proper tabbed sidebar [P2]

**Problem**: Brand DNA, Product DNA, and Mood Board settings all live in a single `showSettings`
boolean toggle that stacks all three sections vertically — this becomes a very long scroll when
data is present. The "⚙ Settings" button label is also an emoji rather than a proper icon.

**Fix**: Replace the stacked layout with a two-column layout when settings are open:

```
[ Workflows list (left col, ~60%) ] [ Settings panel (right col, ~40%) ]
                                       [ Brand | Product | Mood Board tabs ]
```

The tabs use the same `rounded-full px-4 py-1.5` pill pattern from `ChatPanel`. On mobile (< `md`
breakpoint) collapse back to the current stacked layout. Replace the "⚙" emoji with an inline SVG
gear icon matching the existing icon style (`stroke="currentColor"`, `strokeWidth={2}`).

---

### 1d — Mood board thumbnail grid: proper aspect ratio and hover state [P2]

**Problem**: The mood board thumbnail grid uses `h-20 w-full object-cover` images with no hover
interaction and a very small `text-[11px]` caption below each thumbnail.

**Fix**:
- Use `aspect-square` instead of `h-20` so thumbnails are proper squares at any screen width.
- Add a hover overlay (same pattern as the Assets tab in `ChatPanel`) showing the description in
  full on a darkened background: `absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100
  transition-opacity flex items-end p-2`.
- Add `group` to the outer div.

---

### 1e — New Workflow form: inline instead of a pop-down [P2]

**Problem**: The "+ New workflow" button toggles a `showNewWorkflow` state that appends a form
below the button. This causes a layout shift and the form appears disconnected from the button.

**Fix**: Animate the form in with `animate-fade-in-up` and give it a clear visual connection:
```tsx
{showNewWorkflow && (
  <form className="mb-4 animate-fade-in-up rounded-2xl border border-brand-500/30 bg-surface-900
                   p-4 shadow-lg ring-1 ring-brand-500/20" ...>
```
The `ring-1 ring-brand-500/20` and `border-brand-500/30` make it feel like an active creation
state rather than an unrelated panel that appeared.

---

## 2. Login Page (`app/login/page.tsx`)

### 2a — Match the studio's dark theme [P1]

**Problem**: The login page is entirely `bg-neutral-50` / `bg-white`, which clashes with the dark
global body the layout applies.

**Fix**:
- Remove `bg-neutral-50` from the outer container (the body already applies `bg-mesh-dark`).
- Replace the card with: `bg-surface-900/80 border border-surface-700/50 backdrop-blur-xl
  rounded-2xl shadow-2xl`.
- Update all text colors from `text-neutral-500` → `text-surface-400`, inputs from
  `border-neutral-300` → `border-surface-700 bg-surface-800 text-white focus:border-brand-500`.
- Replace `bg-neutral-900 text-white` primary button → `bg-brand-500 text-white hover:bg-brand-600`.
- Change the toggle link from `text-neutral-500 underline` → `text-brand-400 hover:text-brand-300
  transition-colors` (no underline — underlined links feel like a web form, not a studio app).

### 2b — Add the app logo/wordmark above the form [P2]

**Problem**: The form starts with `<h1>Agentic Marketing Studio</h1>` but there is no visual
identity above it — just a plain text heading.

**Fix**: Add a small icon lockup above the `<h1>`:
```tsx
<div className="mb-6 flex flex-col items-center gap-3">
  {/* replace with your real logo SVG or next/image */}
  <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-500
                  shadow-[0_0_20px_rgba(99,102,241,0.5)]">
    <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
    </svg>
  </div>
  <h1 className="text-xl font-semibold text-surface-50">Agentic Marketing Studio</h1>
</div>
```

---

## 3. Studio Page (`app/studio/[sessionId]/page.tsx`)

### 3a — Top bar: group Guardrails / DNA / Style Lock away from Canvas / Node [P1]

**Problem**: The top-left floating bar currently combines three unrelated control groups into two
pill containers: the back button + Canvas/Node toggle in one pill, and Guardrails + DNA + Style
Lock in a second. The pills don't visually differentiate navigation (back button), view modes
(Canvas/Node), and settings (Guardrails/DNA/Style Lock). On narrower viewports the three-pill
layout overflows.

**Fix**: Establish a clear left-to-right hierarchy:
```
[← Workflows]   [Canvas | Node]   [⚙ Guardrails | DNA | 🔒]
  nav back        view toggle           studio settings
```
- Keep the back button as its own standalone pill (already correct).
- Canvas/Node toggle: existing pill, no change.
- Move Guardrails + DNA + Style Lock into a **top-right** pill (absolute `right-6 top-6`) instead
  of continuing the left-side row. This prevents the left bar from getting too long and makes
  settings feel like a panel, not a toolbar.

---

### 3b — User identity in the studio header [P2]

**Problem**: The studio page verifies auth (`me()`) on mount but then does nothing with `user`.
The header has no indication of who is logged in or any way to get back to the account level
without the "← Workflows" button.

**Fix**: Add a minimal user avatar pill in the top-right (alongside the settings pill from 3a):
```tsx
<div className="pointer-events-auto flex items-center gap-2 rounded-full border
                border-surface-700/50 bg-surface-900/40 px-3 py-1.5 text-xs
                font-medium text-surface-300 backdrop-blur-xl shadow-xl">
  <span className="flex h-5 w-5 items-center justify-center rounded-full
                   bg-brand-500 text-[10px] font-semibold text-white">
    {user.username[0].toUpperCase()}
  </span>
  {user.username}
</div>
```

---

### 3c — Loading and auth-error screens: match the dark theme [P1]

**Problem**: Both the loading screen and auth-error screen use `text-neutral-500`, and the error
screen's "Retry" button uses `border-neutral-300 text-neutral-700 hover:bg-neutral-50` — all light
theme values that look wrong on the dark background.

**Fix**: Replace with dark-token equivalents:
```tsx
// Loading
<div className="flex h-screen w-screen items-center justify-center">
  <div className="flex items-center gap-3 text-sm text-surface-400">
    <svg className="h-5 w-5 animate-spin text-brand-500" .../>
    Loading studio…
  </div>
</div>

// Error
<div className="flex h-screen w-screen flex-col items-center justify-center gap-4">
  <p className="text-sm text-surface-400">Could not reach the backend: {authError}</p>
  <button className="rounded-lg border border-surface-700/50 px-4 py-2 text-sm
                     text-surface-200 hover:bg-surface-800/60 transition-colors">
    Retry
  </button>
</div>
```

---

### 3d — Generating state: visible indicator beyond the canvas silhouette [P2]

**Problem**: While `generating === true`, the only visible feedback is the canvas silhouette tile.
No other part of the UI signals that work is in progress — especially important in Node mode where
there's no canvas visible at all.

**Fix**: Add a subtle animated status bar at the very top edge of the page — a 2px progress
shimmer that appears during generation and disappears on completion:
```tsx
{generating && (
  <div className="absolute inset-x-0 top-0 z-50 h-0.5 overflow-hidden">
    <div className="h-full w-1/3 animate-[shimmer_1.5s_ease-in-out_infinite]
                    bg-gradient-to-r from-transparent via-brand-500 to-transparent" />
  </div>
)}
```
Add the `shimmer` keyframe to `tailwind.config.js`:
```js
shimmer: {
  '0%': { transform: 'translateX(-100%) scaleX(3)' },
  '100%': { transform: 'translateX(400%) scaleX(3)' },
},
```

---

### 3e — Modal close button: standardize with a named component [P2]

**Problem**: `showGuardrails` and `showDna` both have identical close button markup (inline SVG
`6 18L18 6M6 6l12 12`). Any future modal adds a third copy.

**Fix**: Extract a `CloseButton` component used by all modals, `StyleLockModal`,
`AdSpecExportModal`, and `CanvasMaskEditorModal`:
```tsx
function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button onClick={onClose}
            className="absolute right-4 top-4 z-10 rounded-full p-1.5 text-surface-400
                       transition-colors hover:bg-surface-800 hover:text-white"
            aria-label="Close">
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
      </svg>
    </button>
  );
}
```

---

## 4. Chat Panel (`components/ChatPanel.tsx`)

### 4a — Hard-coded title: make it the real session title [P1]

**Problem**: The `ChatPanel` header always shows a hard-coded string `"Social Media Video Ads"`
regardless of the actual session title:
```tsx
<h1 className="text-base font-semibold text-white sm:text-lg">Social Media Video Ads</h1>
```
This is clearly a placeholder that was never wired up.

**Fix**: `getSession(sessionId)` is already called in `loadHistory()` — the `SessionResponse`
includes `title`. Store it in a `sessionTitle` state variable and render it:
```tsx
const [sessionTitle, setSessionTitle] = useState<string>("Untitled workflow");
// In loadHistory():
setSessionTitle(session.title ?? "Untitled workflow");
// In render:
<h1 className="text-base font-semibold text-white sm:text-lg">{sessionTitle}</h1>
```
The title is editable via `PUT /api/v1/sessions/{id}` (the `title` field on `CreateSessionRequest`
is also updatable via the same payload shape) — but even just displaying it read-only is a clear
win over the hard-coded string.

---

### 4b — Thinking block: animate the live stream in a distinct style [P1]

**Problem**: `liveThinking` (the live-streaming model text during generation) is only shown inside
the `loading` block's spinner area as a plain italic string alongside a narration line. The
persisted `thinking` text on past messages is in a `<details>` element. Both feel understated for
what is actually a significant UX feature — watching the model think in real time.

**Fix**: Give `liveThinking` its own always-visible block in the chat scroll area (above the
spinner, not inside it):
```tsx
{loading && liveThinking && (
  <div className="animate-fade-in rounded-2xl border border-surface-700/30
                  bg-surface-900/40 px-4 py-3 text-xs text-surface-400 italic
                  max-h-40 overflow-y-auto font-mono leading-relaxed whitespace-pre-wrap">
    {liveThinking}
    <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-brand-500 animate-pulse align-text-bottom" />
  </div>
)}
```
The blinking cursor `span` makes the live stream feel alive without any additional mechanism.

---

### 4c — Narration lines: replace the single last-line with a scrollable stack [P1]

**Problem**: During generation, only `narration[narration.length - 1]` is shown — all previous
narration lines are discarded from view. A user watching generation sees only the current step,
with no trace of what already finished.

**Fix**: Show the last 4 narration lines with older lines faded out:
```tsx
{loading && narration.length > 0 && (
  <div className="flex flex-col gap-1 pl-2">
    {narration.slice(-4).map((line, i, arr) => (
      <span key={i}
            className={`text-xs italic transition-opacity duration-300
                        ${i === arr.length - 1 ? "text-surface-300 opacity-100"
                                               : "text-surface-500 opacity-50"}`}>
        {line}
      </span>
    ))}
  </div>
)}
```

---

### 4d — Option cards: number badges with brand color on active [P2]

**Problem**: The option number badges use `bg-surface-700/50 text-surface-400` for all options
unconditionally, making them look inactive even before any pick is made. The hover state
`hover:scale-[1.01]` is too subtle to feel interactive.

**Fix**:
- On hover, upgrade the badge to `bg-brand-500/20 text-brand-400`.
- Increase hover scale to `hover:scale-[1.02]` and add `hover:border-brand-500/40`.
- Add `hover:shadow-[0_0_12px_rgba(99,102,241,0.2)]` to make the selection feel glowy:
```tsx
className={
  "flex items-center justify-between rounded-xl border px-4 py-2.5 text-left text-sm
   disabled:opacity-50 transition-all group hover:scale-[1.02]
   hover:border-brand-500/40 hover:shadow-[0_0_12px_rgba(99,102,241,0.2)] " +
  (gateStage === "motion_pending" && opt.id === "approve"
    ? "border-red-900/50 bg-red-900/20 hover:bg-red-900/40 text-red-200"
    : "border-surface-700/50 bg-surface-800/80 hover:bg-surface-700 text-surface-200")
}
// Badge inside:
<span className="flex h-6 w-6 items-center justify-center rounded-full
                 bg-surface-700/50 text-xs font-medium text-surface-400
                 group-hover:bg-brand-500/20 group-hover:text-brand-400 transition-colors">
  {i + 1}
</span>
```

---

### 4e — Plan tab: replace the placeholder with real narrative plan content [P2]

**Problem**: The "Plan" tab shows a static placeholder: `"Plan View — We will think about it later."` This is a dead tab. The data it would need already exists: `ChatMessage.narrativePlan`
and `ChatMessage.scenePlan` are already in state.

**Fix**: Render the most recent gate-stage plan data (or the last assistant message's narrative
data if available) in the Plan tab:
```tsx
// Find the most recent gate or completed turn with a narrativePlan
const latestPlan = [...messages].reverse().find(m => m.narrativePlan);

{activeTab === "Plan" && (
  <div className="flex-1 overflow-y-auto p-3 space-y-4">
    {!latestPlan ? (
      <p className="text-sm text-surface-500 text-center mt-8">
        A narrative plan will appear here once generation starts.
      </p>
    ) : (
      <>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-surface-500 mb-2">Shots</p>
          <ol className="space-y-2">
            {latestPlan.narrativePlan!.shots.map((shot, i) => (
              <li key={i} className="flex gap-3 rounded-xl border border-surface-700/50
                                     bg-surface-800/40 p-3 text-xs text-surface-300">
                <span className="shrink-0 font-mono text-brand-400">{String(i+1).padStart(2,'0')}</span>
                {shot}
              </li>
            ))}
          </ol>
        </div>
        <div className="rounded-xl border border-surface-700/50 bg-surface-800/40 p-3">
          <p className="text-xs text-surface-500 mb-1">Overall Story</p>
          <p className="text-sm text-surface-200">{latestPlan.narrativePlan!.overall_story}</p>
        </div>
        {latestPlan.narrativePlan!.pacing_target && (
          <p className="text-xs text-surface-500">
            Pacing: <span className="text-surface-300">{latestPlan.narrativePlan!.pacing_target}</span>
          </p>
        )}
      </>
    )}
  </div>
)}
```

---

### 4f — Send button: visual feedback when input is empty vs. ready [P1]

**Problem**: The send button uses `disabled:opacity-30` when `input` is empty, which is correct,
but the `bg-white text-black` color is abrupt — it looks like a placeholder more than a designed
button.

**Fix**: Transition the button between an "empty" state (muted) and a "ready" state (glowing):
```tsx
<button type="submit" disabled={!input.trim()}
  className={`flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200
              hover:scale-105 shadow-sm
              ${input.trim()
                ? "bg-brand-500 text-white shadow-[0_0_10px_rgba(99,102,241,0.5)]"
                : "bg-surface-700 text-surface-400 opacity-50 cursor-not-allowed"}`}>
  ...arrow svg...
</button>
```
The glow only appears when there is something to send, giving a clear "ready" signal.

---

### 4g — Chat panel aspect ratio: make it responsive [P2]

**Problem**: The chat panel is hard-coded to `aspect-[9/16] w-[340px]` in `studio/page.tsx`. On
viewports below ~400px wide, `340px` overflows the screen. On very tall screens, the `9/16` aspect
ratio wastes vertical space.

**Fix**: Replace the fixed aspect ratio with a fluid min/max height:
```tsx
<div className="pointer-events-none absolute bottom-4 left-4 z-10
                w-[340px] max-w-[calc(100vw-2rem)]
                h-[min(calc(100vh-2rem),600px)] min-h-[400px]">
```
This keeps the panel tall enough to be useful without locking it to a phone-screen aspect ratio
on desktop.

---

### 4h — Empty state: add a suggested prompt [P2]

**Problem**: The empty chat state shows a plain centered paragraph: `"Describe what you want to
build or generate."` There is no invitation to interact.

**Fix**: Add 2–3 clickable suggested prompts that call `handleSend` directly:
```tsx
const SUGGESTIONS = [
  "A hero product shot for a sneaker, clean studio look",
  "A 15-second lifestyle video for a skincare brand",
  "A bold social media banner for a summer sale",
];

<div className="flex h-full flex-col items-center justify-center gap-5 px-2">
  <p className="text-sm text-surface-500 text-center">
    Describe what you want to create, or try one of these:
  </p>
  <div className="flex flex-col gap-2 w-full">
    {SUGGESTIONS.map((s) => (
      <button key={s} onClick={() => handleSend(s)}
              className="rounded-xl border border-surface-700/50 bg-surface-800/40
                         px-4 py-3 text-left text-xs text-surface-300
                         hover:border-brand-500/40 hover:bg-surface-800 transition-all">
        {s}
      </button>
    ))}
  </div>
</div>
```

---

## 5. Canvas Engine (`components/canvas/CanvasEngine.tsx`)

### 5a — Pending generation silhouette: add a pulsing shimmer [P1]

**Problem**: The loading silhouette for a pending generation is a plain `div` with a static
background. Looking at the code, `pendingPos` places it at the right grid position, but the tile
has no animation — it looks like a broken tile, not an in-progress one.

**Fix**: Add a shimmer animation to distinguish it from real content:
```tsx
// In tailwind.config.js add:
shimmerBg: {
  '0%': { backgroundPosition: '-200% 0' },
  '100%': { backgroundPosition: '200% 0' },
},

// On the silhouette div:
className="absolute rounded-2xl overflow-hidden"
style={{ ...positionStyle, backgroundImage: 'linear-gradient(90deg, #27272a 25%, #3f3f46 50%, #27272a 75%)',
         backgroundSize: '200% 100%', animation: 'shimmerBg 1.5s infinite linear' }}
```
Add the matching icon centered in the silhouette based on `pendingGeneration.kind`:
```tsx
const kindIcon = { image: "🖼️", video: "🎬", audio: "🔊", text: "📄" };
```

---

### 5b — Canvas toolbar: label the mode buttons [P1]

**Problem**: The Canvas/Draw mode toggle in the toolbar uses only icon buttons (a cursor and a
pencil SVG) with no labels or tooltips. There is no way to discover these without hovering.

**Fix**: Add `title` attributes for tooltip accessibility, and on hover show a floating label:
```tsx
<button title="Select / Pan (S)" aria-label="Select mode" ...>
  {/* cursor icon */}
</button>
<button title="Draw (D)" aria-label="Draw mode" ...>
  {/* pencil icon */}
</button>
```
Alternatively add visible text labels that collapse on small toolbars:
```tsx
<span className="hidden xl:inline ml-1.5 text-[10px]">Pan</span>
```

---

### 5c — Tile cards: visual hierarchy improvements [P2]

The current tile rendering (`CanvasEngine.tsx`) is functional but has some polish opportunities:

1. **Compliance status badge**: Currently shown as a small colored dot. Replace with a pill badge
   that uses the same token pattern as the rest of the app:
   ```tsx
   // passed: emerald, failed: red, running: pulsing blue
   <span className={`absolute top-2 right-2 rounded-full border px-1.5 py-0.5 text-[9px]
                     font-medium backdrop-blur-sm ${statusClasses}`}>
     {complianceStatus}
   </span>
   ```

2. **producedBy label**: The specialist name shown on hover is currently raw text. Truncate and
   capitalize it: `illustrator` → `Illustrator`. Use `el.producedBy.replace(/_/g, " ").replace
   (/\b\w/g, c => c.toUpperCase())`.

3. **Version badge**: Version numbers on tiles (e.g. `v3`) should use `bg-surface-900/70
   backdrop-blur-sm` so they remain legible over both light and dark image regions.

4. **Selected (referenced) tile glow**: The currently selected tile highlight should use the
   established `shadow-[0_0_15px_rgba(99,102,241,0.5)]` brand glow pattern (already used on
   buttons in `page.tsx`) instead of a plain outline — making the "referenced" state feel
   consistent with selection elsewhere in the UI.

---

### 5d — Timeline group frames: add a "generated at" timestamp label [P2]

**Problem**: `computeTimelineFrames` already computes a label string (date range, time range,
specialist names, item count) for each run group. But the label is rendered as small plain text
inside the dashed frame border with no visual separation from the frame border itself.

**Fix**: Render the label as a floating tag above the top-left corner of the frame (outside the
dashed border, not inside it):
```tsx
// Position the label div at top: `box.y - 24px`, left: `box.x`
<div style={{ position: 'absolute', left: box.x, top: box.y - 24, transform: 'none' }}
     className="rounded-t-lg bg-surface-900/80 border border-b-0 border-surface-700/40
                px-2.5 py-0.5 text-[10px] font-medium text-surface-400 backdrop-blur-sm">
  {frame.label}
</div>
```

---

## 6. Node Graph View (`components/NodeGraphView.tsx`)

### 6a — Node cards: real icons per specialist kind [P2]

**Problem**: `NodeCard` uses emoji icons (`🔍`, `🎨`, `✍️` etc.) per `node.kind`. These look
inconsistent with the SVG-icon design language used everywhere else in the app.

**Fix**: Replace emojis with a dedicated inline SVG per `kind` value
(`"ideation"`, `"orchestrator"`, `"lead"`, `"specialist"`). Use the same `stroke="currentColor"
strokeWidth={2}` style:

| Kind | Icon |
|---|---|
| `ideation` | Lightbulb (`M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3M6.343 6.343l-.707-.707M6.343 17.657l-.707.707...`) |
| `orchestrator` | Arrows branching (`M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4`) |
| `lead` | Users group |
| `specialist` | Cog / tool |

---

### 6b — Connecting arrows between waves: replace Unicode arrows with SVG lines [P2]

**Problem**: Arrows between pipeline nodes currently use `→` Unicode characters rendered as text.
They don't curve, can't carry status color, and look out of place on a canvas-style view.

**Fix**: Render actual SVG `<line>` or `<path>` elements connecting each node's right edge to the
next node's left edge. Use `stroke="rgba(63,63,70,0.6)"` (surface-700) for standard connections,
`stroke="rgba(99,102,241,0.5)"` (brand-500/50) for edges leading into the currently running node:
```tsx
// Compute screen coords from world coords using camera transform
// Draw: <svg style={{ position:'absolute', inset:0, pointerEvents:'none', width:'100%', height:'100%' }}>
//   <line x1={...} y1={...} x2={...} y2={...} stroke="..." strokeWidth="1.5" strokeDasharray="4 4"/>
// </svg>
```

---

### 6c — Thinking text: make it a proper monospace stream [P1]

**Problem**: The `thinking` text inside each `NodeCard` uses `text-[10px] text-surface-500 italic`
in a scrollable block. Raw model output in italic at 10px is nearly unreadable and doesn't look
like a stream.

**Fix**: Use `font-mono text-[10px] text-surface-400 leading-relaxed not-italic`. Add a live
blinking cursor `span` when the node's `status === "running"`:
```tsx
<div className="mt-2 max-h-40 overflow-y-auto rounded-lg bg-surface-950/50 p-2
                font-mono text-[10px] text-surface-400 leading-relaxed whitespace-pre-wrap">
  {node.thinking}
  {node.status === "running" && (
    <span className="inline-block w-1.5 h-3 ml-0.5 bg-brand-500 animate-pulse align-text-bottom"/>
  )}
</div>
```

---

## 7. Shared Modals (`CanvasMaskEditorModal`, `AdSpecExportModal`, `StyleLockModal`)

### 7a — Mask editor: brush cursor feedback [P2]

**Problem**: `CanvasMaskEditorModal` uses a `<canvas>` element for brush drawing but the CSS
cursor is not set to `crosshair` — the default `auto` cursor shows a text cursor over canvas,
which is confusing.

**Fix**: Add `cursor={isDrawingRef.current ? "crosshair" : "default"}` via a state variable, or
statically set `style={{ cursor: "crosshair" }}` on the overlay canvas element.

---

### 7b — Ad spec export: preview thumbnails [P2]

**Problem**: `AdSpecExportModal` lists platform spec names (`Meta Feed 1:1`, `IG Story 9:16`) and
a "Generate All" button. After generation, it shows download links. There are no visual previews
of what each spec looks like.

**Fix**: Once the export result is available (the `exports` dict maps spec name → storage_ref),
render a `<img src={assetUrl(storageRef)} .../>` thumbnail grid (same `aspect-[1/1]` or
`aspect-[9/16]` as the spec's actual ratio) before the download link. Use the already-imported
`assetUrl` from `lib/http`.

---

### 7c — Style Lock modal: preview the current style reference image [P2]

**Problem**: `StyleLockModal` has an input for `style_ref_storage_ref` (a text field) but no
visual preview of the referenced image. The user types a raw storage ref with no feedback.

**Fix**: When `styleRefStorageRef` is non-empty, render a small preview image:
```tsx
{styleRefStorageRef && (
  <img src={assetUrl(styleRefStorageRef)}
       className="mt-2 h-24 w-full rounded-xl border border-surface-700 object-cover"
       alt="Style reference preview" />
)}
```

---

## 8. Global / Cross-Cutting

### 8a — Loading and empty states: add a spinner component [P1]

**Problem**: There are at least 6 different inline loading spinners across the codebase, all
copying the same SVG circle/path pattern. Each is slightly different (some have `mr-2`, some have
`text-surface-600`, one has `text-brand-500`).

**Fix**: Extract a single `Spinner` component to `components/Spinner.tsx`:
```tsx
export function Spinner({ size = 5, className = "" }: { size?: number; className?: string }) {
  return (
    <svg className={`h-${size} w-${size} animate-spin text-brand-500 ${className}`}
         fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10"
              stroke="currentColor" strokeWidth="4"/>
      <path className="opacity-75" fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014
               12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
    </svg>
  );
}
```
Replace all inline spinner SVGs with `<Spinner />`.

---

### 8b — Error states: unified `ErrorBanner` component [P2]

**Problem**: There are multiple error display patterns: `text-red-700` on the home page (light
theme), `bg-red-900/80 text-red-200` on the canvas overlay, and `text-sm text-red-700` in the
settings forms — all slightly different.

**Fix**: Extract `components/ErrorBanner.tsx`:
```tsx
export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-red-700/40
                    bg-red-900/20 px-4 py-2.5 text-sm text-red-300">
      <span>{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="shrink-0 text-red-400 hover:text-red-200 transition-colors">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      )}
    </div>
  );
}
```

---

### 8c — `window.prompt()` usage: replace with inline modals [P2]

**Problem**: Three UI actions use `window.prompt()`:
1. Canvas right-click "New Image/Video/Audio" in `studio/page.tsx`
2. "Regenerate" in `CanvasView.tsx`
3. "Comment" in `CanvasView.tsx`

`window.prompt()` is a native browser dialog — it blocks the entire tab, has no styling, shows
the page origin (ugly), and cannot be customized. It breaks the studio's dark theme completely.

**Fix**: Replace all three with a small inline `PromptModal` component. It can be a single
reusable modal that accepts a `label`, `placeholder`, and `onConfirm(value)` callback:
```tsx
interface PromptModalProps {
  isOpen: boolean;
  label: string;
  placeholder?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}
```
Render it with the same `fixed inset-0 z-50 bg-black/60 backdrop-blur-sm` overlay pattern used
by all other modals. The input uses the established dark input style. This is a purely visual
replacement — the behavior (collect a string, call a handler) is identical.

---

### 8d — `<img>` vs `<Image>` for static assets [P3]

**Problem**: The codebase uses `<img>` (with `// eslint-disable-next-line @next/next/no-img-element`
suppression comments) for dynamic, backend-served assets. This is correct — `next/image` cannot
optimize URLs from an external API server. However, any future static assets (logo, placeholder
images) should use `next/image` to benefit from Next.js automatic optimization.

**Fix**: Document this distinction in `CLAUDE.md` / `AGENTS.md`:
- Dynamic API assets (`assetUrl(storageRef)`) → `<img>` with the suppression comment (correct)
- Static app assets (logo, illustrations) → `next/image` (better performance)

---

### 8e — Keyboard shortcut: `Esc` to close modals [P3]

**Problem**: `StyleLockModal`, `AdSpecExportModal`, `CanvasMaskEditorModal` have no keyboard
close handler. The Guardrails and DNA modals in `studio/page.tsx` are closed by clicking the
background overlay (via `window.addEventListener("pointerdown", close)`) but not by `Escape`.

**Fix**: Add a standard `useEffect` in each modal that listens for `keydown` with `key === "Escape"` and calls `onClose`:
```tsx
useEffect(() => {
  if (!isOpen) return;
  const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
  window.addEventListener("keydown", handler);
  return () => window.removeEventListener("keydown", handler);
}, [isOpen, onClose]);
```

---

### 8f — Tailwind `animate-in` / `slide-in-from-bottom-2` usage [P3]

**Problem**: `ChatPanel.tsx` uses `animate-in fade-in slide-in-from-bottom-2 duration-300` on
chat messages — but these are Tailwind CSS Animate classes (from the `tailwindcss-animate`
plugin), which is **not listed in `package.json`**. These classes likely have no effect at runtime
because the plugin is not installed.

**Fix**: Either:
1. Install `tailwindcss-animate` (`npm install tailwindcss-animate`) and add it to
   `tailwind.config.js` `plugins: [require("tailwindcss-animate")]` — the simplest fix.
2. Or replace with the custom `animate-fade-in-up` already defined in `tailwind.config.js`.

---

---

## 9. Next-Gen Immersive & Interactive Agentic Flow (Phase 3)

These recommendations elevate the studio from a standard dashboard into an **immersive, living creative studio** where agent collaboration is visible, tactile, and deeply interactive.

---

### 9a — Agent HUD: Live "Working" Avatars & Canvas Activity Beams [P1]

**Concept**: When agents are running in the pipeline (Ideator, Copywriter, Illustrator, Motion Gen, Voice/Audio, Compliance), their presence should be visually tangible on the canvas.
* **Specialist Persona Badges**: Display distinct animated status avatars for active agents:
  * 🎬 **Creative Director** (Ideating & Orchestrating)
  * ✍️ **Copywriter** (Drafting narrative scripts)
  * 🎨 **Art Specialist** (Generating visuals & style references)
  * 🎥 **Motion Engine** (Video rendering & transitions)
  * 🎙️ **Voice & Audio** (Synthesizing Kokoro speech & background tracks)
  * 🛡️ **Brand Guard** (Auditing compliance & guardrails)
* **Canvas Beacon / Laser Focus**: When an agent is generating or auditing a specific tile, render a subtle glowing radar beacon around that tile showing the agent's avatar and action label (e.g., *"Art Specialist is painting scene 2..."*).
* **Impact**: Turns waiting for generation into an exciting spectator experience where you watch the autonomous team collaborate.

---

### 9b — Interactive Storyboard & Multi-Track Campaign Sequencer [P1]

**Concept**: A collapsible bottom docking bar that sequences all generated video shots, voiceover clips, and soundtrack layers into a unified playable timeline.
* **Multi-Track Timeline**:
  * **Track 1 (Visuals/Video)**: Scrubbable thumbnails of scene video clips in order.
  * **Track 2 (Voiceover)**: Audio waveform tiles aligned to corresponding video scenes.
  * **Track 3 (Music/SFX)**: Background audio layer with volume envelope.
* **Integrated Canvas Mini-Player**: A floating preview monitor that synchronizes and plays the generated video clips + synthesized audio together before exporting.
* **Impact**: Solves the biggest friction in generative video marketing—seeing how individual clips and sound fit together as a cohesive ad campaign.

---

### 9c — Contextual Tile Radial Menu & Visual Region Inpainting [P2]

**Concept**: Clicking or hovering any asset on the canvas reveals a fluid floating action ring:
* **One-Click Quick Actions**:
  * 🪄 **Remix / Variations**: Spawns 3 alternative visual directions directly adjacent to the tile.
  * 🔄 **Extend (Video)**: Extends the shot forward using the last frame.
  * 🎨 **Style Transfer**: Locks this tile's aesthetic to the Style Lock repository.
  * ✂️ **Inpaint Mask**: Directly opens the mask editor without navigating submenus.
* **Visual Canvas Pin-Drops**: Allow the user to click anywhere on an image or video frame to drop a pin with feedback (e.g., *"Change this lighting to neon cyberpunk"*), which feeds directly into the agent prompt with coordinates.
* **Impact**: Eliminates typing long descriptive text prompts by enabling direct spatial canvas interactions.

---

### 9d — Immersive Split-Screen & Node Radar (Mini-Map) [P2]

**Concept**: Flexible workspace layouts tailored to high-power workflows:
* **Canvas + Node Graph Split Mode**: Drag-adjustable split view allowing users to see the output canvas on the left while monitoring live pipeline node execution on the right.
* **Node Radar (Picture-in-Picture Mini-Map)**: A collapsible mini-map in the bottom-right corner showing a bird’s-eye view of all pipeline steps and spatial tile clusters on the canvas.
* **Zen Cinema Mode (Key shortcut `F`)**: Auto-hides all navigation bars, sidebars, and toolbars, leaving an edge-to-edge canvas with subtle floating controls for presentations and client reviews.
* **Impact**: Gives power users total control over their screen real estate and makes workflow orchestration crystal clear.

---

### 9e — Live Brand DNA & Guardrail Confidence Radar [P2]

**Concept**: A real-time visual telemetry widget that monitors brand safety and aesthetic compliance as assets are generated.
* **Real-Time Compliance Ring**: A circular dial showing an overall **Brand Alignment Score (0–100%)**.
* **Live Constraint Flags**:
  * 🟢 Palette match (`#6366f1` brand colors verified)
  * 🟢 Logo placement safe-zone compliant
  * 🟡 Pacing slightly faster than target
* **Click-to-Inspect**: Clicking the dial smoothly opens the Guardrails drawer directly to the flagged rule with 1-click auto-fix options.
* **Impact**: Builds immense trust in agentic outputs by providing transparent, visual safety guarantees.

---

### 9f — Side-by-Side Variant A/B Comparison Deck [P3]

**Concept**: When multiple candidate shots or ad variations are generated, dragging one tile over another opens an A/B Comparison Deck.
* **Interactive Curtain Slider**: Slide left/right across two image or video variations to inspect fine detail differences.
* **Metadata Diff**: Side-by-side inspection of prompt parameters, seed, model provider, and compliance scores.
* **Pick & Promote**: 1-click button to select the winning variant, automatically archiving superseded drafts into a collapsed stack.

---

## Summary: Quick-Win Priority Order

| ID | Change | Effort | Impact |
|---|---|---|---|
| 1a | Dark theme on home page | Low | High |
| 4a | Wire real session title in ChatPanel | Very Low | High |
| 3c | Dark theme on loading/error screens | Very Low | High |
| 2a | Dark theme on login page | Low | High |
| 9a | **Agent HUD: Live Specialist Avatars & Canvas Activity Beams** | Medium | High |
| 9b | **Interactive Storyboard & Campaign Sequencer Dock** | Medium | Very High |
| 9c | **Contextual Tile Radial Actions & Direct Visual Pin-Drops** | Medium | High |
| 9d | **Canvas + Node Split View & Mini-Map Radar** | Medium | High |
| 9e | **Live Brand DNA & Guardrail Confidence Dial** | Low | Medium |
| 9f | **Side-by-Side Variant A/B Comparison Deck** | Medium | Medium |
| 4f | Send button visual state | Very Low | Medium |
| 4c | Narration: show last N lines, not just last 1 | Very Low | Medium |
| 4b | Live thinking: blinking cursor + own block | Low | Medium |
| 8f | Fix missing `tailwindcss-animate` plugin | Very Low | Medium |
| 8a | Extract `Spinner` component | Low | Medium |
| 1b | Workflow status badges | Low | Medium |
| 5a | Canvas silhouette shimmer animation | Low | Medium |
| 4h | Empty state: suggested prompts | Low | Medium |
| 8c | Replace `window.prompt()` with inline modals | Medium | High |
| 4e | Plan tab: wire real narrative plan | Medium | Medium |
| 3a | Studio top bar layout restructure | Medium | Medium |
| 1c | Settings sidebar tab layout | Medium | Medium |
| 7c | Style Lock: style reference preview | Very Low | Medium |
| 4g | Chat panel: fluid height, not fixed aspect ratio | Low | Low |
| 8e | `Esc` key to close all modals | Very Low | Low |
| 5b | Canvas toolbar button tooltips | Very Low | Low |
| 3b | User identity pill in studio header | Low | Low |
| 7a | Mask editor crosshair cursor | Very Low | Low |
| 6c | Node graph: monospace thinking text | Low | Low |
| 3d | Generating: shimmer progress bar | Low | Low |

