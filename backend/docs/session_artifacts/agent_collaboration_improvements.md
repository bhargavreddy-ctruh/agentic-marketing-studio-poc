# Assessment: True File Sharing & Parallel Agent Collaboration

Based on a review of the current orchestration (`graph.py`) and lead definitions (e.g., `visual_design_lead.py`, `scene_lead.py`), here is an analysis of how agents currently share files and what must change to achieve **true parallel collaboration and bidirectional feedback loops.**

---

## 1. How It Already Is (Current State)

### **A. Sequential, Hardcoded Python Flow**
Right now, specialists do **not** truly "talk" to each other. They are orchestrated by rigid Python functions.
For example, in `scene_lead.py`:
1. Python calls `environment_designer` and waits for it to generate an image (`storage_ref`).
2. Python extracts that `storage_ref` and inserts it as a string into the context for `prop_stylist` (`f"Current image storage_ref: {storage_ref}"`).
3. Python calls `prop_stylist`.

### **B. No Backward Communication (The "Send it back" Problem)**
If Agent 2 (`prop_stylist`) gets an image from Agent 1 (`environment_designer`) that its tools cannot parse or edit, it simply fails and throws a `SpecialistFailed` exception. 
- **Current Behavior:** The Python code catches the exception, logs a warning, and either aborts the turn or skips the edit (e.g., keeping the unrefined image). 
- **Missing Behavior:** Agent 2 cannot say, *"Hey Environment Designer, your image caused an error in my editor tool. Please regenerate it with a less cluttered background."*

### **C. Limited Parallelism**
Currently, `graph.py` implements parallelism via `_run_multi_generation`. However, this is used to generate **independent variants** (e.g., "make 3 different posters at the same time"). It is **not** used to have different specialists work concurrently on a shared goal (e.g., having a Voiceover Artist and a Video Animator work simultaneously on the same scene plan).

---

## 2. What Needs to Be Improved & Changed

To achieve true bidirectional file sharing and parallel collaboration, we need to move away from rigid procedural Python scripts and leverage **LangGraph's multi-agent routing capabilities**.

### **Change 1: Shared Artifact State (The "Workspace")**
Instead of passing `storage_ref` strings hidden in text prompts, the LangGraph `State` must include a shared `assets` dictionary. 
*   **How it works:** When an agent creates a file, they publish it to the shared state (e.g., `assets["scene_1_base"] = "storage_ref_XYZ"`). Any parallel agent can immediately subscribe to and access this artifact.

### **Change 2: Bidirectional Feedback Loops (The "Send it Back" Mechanic)**
We need to replace the rigid `A -> B -> C` pipeline with a dynamic routing sub-graph for each Lead.
*   **How it works:** 
    1. Agent 1 generates the file.
    2. Agent 2 tries to use its tools on the file. If the tool throws an error (or if Agent 2 decides the file is bad), Agent 2 outputs a specific response: `{"action": "reject", "target": "agent_1", "reason": "Tool failed with error X. Please regenerate..."}`.
    3. The LangGraph conditional edge routes execution **back** to Agent 1 with Agent 2's feedback.
    4. Agent 1 regenerates and passes the new file back to Agent 2.

### **Change 3: True Parallel Execution (Fan-out / Fan-in)**
Leads should operate truly concurrently when working toward a common goal, only syncing when necessary.
*   **How it works:** Once a `ScenePlan` is finalized by the Campaign Director, the graph uses LangGraph's parallel fan-out:
    *   **Path A:** `Narrative_Lead` generates the Voiceover audio file.
    *   **Path B:** `Visual_Design_Lead` generates the image assets.
    *   **Path C:** `Motion_Lead` animates the video.
    *   All three paths run simultaneously. When all finish, a `Fan-in` node (like an `Assembly_Lead`) collects the audio, image, and video `storage_ref`s and muxes them together using the `Audio_Video_Muxer` tool.

---

## 3. Implementation Roadmap

To implement this, we need to refactor the orchestration:

1. **Refactor Lead Pipelines into Sub-Graphs:** Convert `visual_design_lead.py` and `scene_lead.py` from linear `async def` functions into their own LangGraph sub-graphs. 
2. **Implement the "Review/Reject" Edge:** Add conditional logic (`add_conditional_edges`) to these sub-graphs so that any node can return a `reject` action that routes execution backward to the offending upstream node.
3. **Upgrade `SpecialistFailed` Handling:** Instead of catching `SpecialistFailed` and giving up, the `run_specialist_agentic` wrapper should catch tool errors and feed the exact error string *back* to the specialist in a loop, allowing the specialist itself to decide if it should try a different approach or bounce the task back to the previous agent.
4. **Parallel Fan-out in `graph.py`:** Update the main `GraphState` to support `Send()` API (LangGraph's dynamic parallel mapping) to launch Audio, Video, and Image generation concurrently once the planning phase is done.
