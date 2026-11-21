"""Rate limit for the widgets API: 100 requests per client in one minute."""

LIMIT = 100


def handle_excess(client, request):
    # TODO(#41): Decide what happens to a request over the limit.
    raise NotImplementedError
