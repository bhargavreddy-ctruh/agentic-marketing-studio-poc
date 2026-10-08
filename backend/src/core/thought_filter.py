class ThoughtFilter:
    def __init__(self, emit_fn, node_name):
        self.emit = emit_fn
        self.node = node_name
        self.buffer = ""
        self.in_thought = False
        self.thought_tag = "<thought>"
        self.close_tag = "</thought>"
        
    def on_delta(self, delta: str):
        self.buffer += delta
        
        while self.buffer:
            if not self.in_thought:
                # Look for <thought>
                idx = self.buffer.find(self.thought_tag)
                if idx != -1:
                    self.in_thought = True
                    self.buffer = self.buffer[idx + len(self.thought_tag):]
                else:
                    # Not found. If buffer might be part of the tag, keep it.
                    # Otherwise discard.
                    match = False
                    for i in range(1, len(self.thought_tag)):
                        if self.buffer.endswith(self.thought_tag[:i]):
                            self.buffer = self.buffer[-i:]
                            match = True
                            break
                    if not match:
                        self.buffer = ""
                    break
            else:
                # Look for </thought>
                idx = self.buffer.find(self.close_tag)
                if idx != -1:
                    # Emit up to the tag
                    if idx > 0:
                        self.emit(node=self.node, text=self.buffer[:idx])
                    self.in_thought = False
                    self.buffer = self.buffer[idx + len(self.close_tag):]
                else:
                    # Emit everything except a potential partial </thought> tag at the end
                    match_len = 0
                    for i in range(1, len(self.close_tag)):
                        if self.buffer.endswith(self.close_tag[:i]):
                            match_len = i
                            break
                    if match_len > 0:
                        emit_text = self.buffer[:-match_len]
                        self.buffer = self.buffer[-match_len:]
                    else:
                        emit_text = self.buffer
                        self.buffer = ""
                    if emit_text:
                        self.emit(node=self.node, text=emit_text)
