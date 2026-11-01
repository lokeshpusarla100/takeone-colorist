"""Normalize provider accounting; unknown counts stay unknown, never estimated."""


def usage_details(usage):
    source = usage if isinstance(usage, dict) else {}

    def count(value):
        return value if type(value) is int and value >= 0 else None

    def field(*keys):
        return next((value for key in keys if (value := count(source.get(key))) is not None), None)

    def nested(*keys):
        for parent, child in keys:
            value = source.get(parent)
            if isinstance(value, dict) and count(value.get(child)) is not None:
                return count(value[child])
        # Also accept already-normalized failure accounting.
        return count(source.get(keys[0][1]))

    incoming, outgoing = field("input_tokens", "prompt_tokens"), field("output_tokens", "completion_tokens")
    total = field("total_tokens")
    if total is None and incoming is not None and outgoing is not None:
        total = incoming + outgoing
    return {"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": total,
            "cached_tokens": nested(("input_tokens_details", "cached_tokens"), ("prompt_tokens_details", "cached_tokens")),
            "reasoning_tokens": nested(("output_tokens_details", "reasoning_tokens"), ("completion_tokens_details", "reasoning_tokens"))}
