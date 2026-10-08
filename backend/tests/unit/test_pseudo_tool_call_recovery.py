from src.services.specialists.runner import _recover_pseudo_tool_call


def test_recovers_json_tool_call_with_a_stray_unescaped_quote_in_arguments():
    # Real, live-found failure (2026-10-07): this exact shape — a JSON-wrapped pseudo tool-call
    # whose `arguments.prompt` has an un-escaped quote describing on-screen text — used to fail
    # recovery entirely (bare `json.loads` had none of `extract_json`'s hardening), discarding an
    # otherwise clearly-recoverable, well-intentioned model response.
    text = (
        '{"tool_call": {"name": "high_resolution_image_generator", '
        '"arguments": {"prompt": "A striking Instagram post with a banner reading "SALE" in bold neon"}}}'
    )
    result = _recover_pseudo_tool_call(text, allowed_tools=("high_resolution_image_generator",))
    assert result is not None
    name, args = result
    assert name == "high_resolution_image_generator"
    assert "SALE" in args["prompt"]


def test_does_not_recover_a_tool_not_in_allowed_tools():
    text = '{"tool_call": {"name": "not_allowed_tool", "arguments": {"prompt": "x"}}}'
    assert _recover_pseudo_tool_call(text, allowed_tools=("illustrator",)) is None
