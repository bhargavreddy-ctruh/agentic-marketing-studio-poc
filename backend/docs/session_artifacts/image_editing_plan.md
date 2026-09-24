# Bug Fix & Image Editing Implementation Plan

I have investigated the two issues you reported regarding image editing and the chat bot referencing bug. Here is what is causing them and how I will fix them:

## 1. Chat Bot Referencing Bug ("answering for another image")
**The Issue:** 
When you select an image on the canvas (e.g., Image A) and send a chat message, the frontend is supposed to clear your selection immediately so it doesn't bleed into your next message. However, the `onClearReference` callback was never wired up in `page.tsx`! As a result, if you later click Image B, the chat bot receives *both* Image A and Image B in its context, confusing the AI about which one you actually want to edit.

**The Fix:**
- **[MODIFY] `frontend/app/studio/[sessionId]/page.tsx`**: I will wire up `onClearReference` to correctly clear the `referencedElements` state. When you send a message or click the "clear" button, it will properly deselect the image so it doesn't haunt future turns.

## 2. Image Edit Tool Integration
**The Issue:**
When you ask the AI to "edit this image", the orchestrator correctly routes the request to the `composition_artist` and provides the referenced image. However, the prompt for `composition_artist` (`composition_artist.md`) strictly forces it to return a JSON object (`aspect_ratio` and `framing`) and *explicitly forbids* it from calling tools! Because of this, it never actually calls the `image_editor` tool.

**The Fix:**
- **[MODIFY] `backend/src/services/specialists/prompts/composition_artist.md`**: I will completely rewrite the Composition Artist's prompt. Instead of forcing a JSON response, I will instruct it to act as an agentic image editor. 
- It will be instructed to call the `image_editor` tool when it needs to apply a targeted edit to an existing asset (passing the `storage_ref` and the user's instruction).
- It will also be instructed to call the `text_card_writer` tool to output a creative brief when required.

## Verification Plan
1. **Frontend**: Select an image, send a chat message, and verify that the selection is immediately cleared from the chat input.
2. **Backend**: Select an image, type "Edit this image to make it darker", and verify that the `composition_artist` successfully calls the `image_editor` tool with the `storage_ref` of the selected image, resulting in a new image tile on the canvas.
