# Prompt & Tier 1 Model Analysis Report

This report addresses the decision-making and tool-calling issues you've been experiencing with the agents, specialists, and orchestrator. 

## 1. Prompt Analysis & Improvement Recommendations

I have reviewed the prompts powering your Orchestrator (`orchestrator.py`), Classifier (`specialist_classifier.py`), and various Specialists (e.g., `illustrator.md`, `overlay_artist.md`). 

### The Core Problem: "Prompt Noise" and Negative Constraints
Currently, your system prompts are **acting as both instructions for the LLM and developer documentation**. 
They are filled with historical anecdotes and justifications for rules (e.g., *"A real, live-found failure this guards against (2026-09-21)..."* or *"never doing this because of..."*). 

Smaller models (like a 3B parameter model) get easily confused by this. When an LLM reads a long paragraph explaining a past failure, it often loses track of what it is *actually supposed to do right now*. Furthermore, relying heavily on negative constraints ("never do X") often causes small LLMs to hyper-fixate on "X" and accidentally trigger it.

### Recommended Fixes for Prompts:

1. **Remove the Developer History:** 
   Strip out all the justifications, dates, and stories about past bugs from the prompts. Keep that information as Python comments in the codebase, but do not send it to the LLM. 
2. **Shift to Positive, Step-by-Step Instructions:**
   Instead of writing *"Calling tool X is not the end of your turn, you must also call Y"*, use explicit, numbered sequences:
   - *Step 1: If math is needed, call `discount_math_calculator`.*
   - *Step 2: You MUST call `text_overlay` with the result.*
3. **Use Markdown/XML Tag Structuring:**
   Break down large walls of text into clearly demarcated sections.
   ```xml
   <role>You are the Overlay Artist.</role>
   <rules>
     1. Always use final numbers provided by the user.
     2. If calculation is needed, call `discount_math_calculator`.
   </rules>
   <output_format>
     Return ONLY valid JSON...
   </output_format>
   ```
4. **Add Few-Shot Examples:**
   Instead of explaining the difference between `direct_fix` and `full_video` in two long paragraphs, provide 3 short input-output examples. Small models learn significantly better from patterns than from abstract rules.

---

## 2. Tier 1 Model Recommendations

You are currently using **Qwen 2.5 3B** for Tier 1 tasks (like routing and tool decisions). While Qwen 2.5 is a great architecture, a **3B parameter model fundamentally lacks the reasoning and context-window comprehension** required to parse complex, multi-tool instructions and strict JSON formatting consistently. It is simply too small for reliable agentic tool-calling.

### Recommended Local/Open-Source Replacements:

If you are running this locally and need a fast, efficient model, consider these upgrades:

1. **Llama 3.1 8B Instruct (Meta)**
   - **Why:** Llama 3.1 8B is currently the gold standard for small, local models. It was explicitly fine-tuned for tool-calling and JSON adherence. It will handle the orchestrator's routing and specialist tool selection vastly better than a 3B model, while still being small enough to run locally on consumer hardware.
2. **Qwen 2.5 7B or 14B (Alibaba)**
   - **Why:** Since you are already in the Qwen ecosystem, simply stepping up to the 7B or 14B parameter versions will yield massive improvements. The 7B model requires roughly 4-5GB of VRAM (quantized), which is very manageable, and its reasoning capabilities are exponentially better than the 3B version.
3. **Gemma 2 9B (Google DeepMind)**
   - **Why:** Gemma 2 punches far above its weight class in logic and reasoning benchmarks. It is exceptionally good at following strict instructions and formatting.

### Recommended Cloud/API Replacements (If not restricted to local):

If your Tier 1 model does not strictly need to be self-hosted, you can achieve near-perfect routing and tool-calling with extremely low latency and cost using these API models:

1. **Gemini 1.5 Flash (Google)**
   - **Why:** Insanely fast, very cheap, and possesses a massive context window. It has native, state-of-the-art function calling capabilities.
2. **Claude 3.5 Haiku (Anthropic)**
   - **Why:** Known for being the fastest, smartest model in its price tier. It is brilliant at following complex XML/Markdown instructions and chaining tools correctly.

### Summary
To immediately fix the agent decision-making:
1. **Upgrade the Tier 1 Model:** Swap Qwen 2.5 3B for **Llama 3.1 8B** or **Qwen 2.5 7B**.
2. **Refactor Prompts:** Remove all developer logs/anecdotes from the system prompts, structure them with XML tags, and add 2-3 concrete examples of correct routing.
