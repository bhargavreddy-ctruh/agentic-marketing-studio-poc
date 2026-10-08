from src.core.json_extract import extract_json


def test_thought_block_containing_braces_does_not_corrupt_the_real_json():
    text = (
        '<thought>\nThe user wants something like {"example": "not real"} in their message, '
        "so I should plan carefully.\n</thought>\n"
        '{"ready": true, "idea": "new launch overlay"}'
    )
    assert extract_json(text) == {"ready": True, "idea": "new launch overlay"}


def test_thought_block_without_braces_still_works():
    text = "<thought>Just reasoning, nothing tricky here.</thought>\n" '{"ready": false}'
    assert extract_json(text) == {"ready": False}


def test_unclosed_thought_block_falls_back_to_whole_text():
    text = '<thought>\nStill thinking, got cut off before any JSON {"a": 1}'
    # No real JSON exists after stripping — falls back to the original text's own brace match.
    assert extract_json(text) == {"a": 1}


def test_literal_raw_newline_inside_a_string_value_does_not_crash_parsing():
    # Real, live-found failure (2026-10-06, overlay_artist): the model wrote a literal newline
    # inside a `"reasoning"` string value instead of an escaped `\n` — strict JSON rejects this
    # with "Invalid control character", even though it's otherwise well-formed.
    text = '{\n  "needs_overlay": true,\n  "reasoning": "Line one\nLine two"\n}'
    assert extract_json(text) == {"needs_overlay": True, "reasoning": "Line one\nLine two"}


def test_stray_unescaped_quote_inside_a_string_value_does_not_crash_parsing():
    # Real, live-found failure (2026-10-07, illustrator's pseudo tool-call): the model's prompt
    # text described on-screen copy with a literal, un-escaped quote (`a sign reading "SALE"`)
    # instead of escaping it as `\"SALE\"` — this used to fail with "Expecting ',' delimiter"
    # hundreds of characters in, discarding an otherwise well-formed, clearly-intentioned response.
    text = '{"tool_call": {"name": "illustrator", "arguments": {"prompt": "a sign reading "SALE" in neon"}}}'
    assert extract_json(text) == {
        "tool_call": {"name": "illustrator", "arguments": {"prompt": 'a sign reading "SALE" in neon'}}
    }


def test_stray_quote_repair_leaves_genuinely_well_formed_json_untouched():
    # The repair must never fire (or must be a no-op) on already-valid JSON — only ever kick in
    # once normal strict parsing has already failed.
    text = '{"answer": "the price is \\"not available\\" right now"}'
    assert extract_json(text) == {"answer": 'the price is "not available" right now'}
