"""Azure Function: Event Hubs trigger → ADLS Bronze writer + Feast materialiser.

Reads events from Azure Event Hubs, validates schema, writes raw events
to ADLS Gen2 Bronze container, and materialises online features to Postgres
via Feast.
"""

# TODO Day 7: Implement Azure Function

import azure.functions as func

app = func.FunctionApp()


@app.event_hub_message_trigger(
    arg_name="event",
    event_hub_name="fraud-transactions",
    connection="EVENT_HUB_CONNECTION",
)
async def process_transaction(event: func.EventHubEvent) -> None:
    """Process incoming transaction event from Event Hubs."""
    raise NotImplementedError("Day 7: Implement event processing")
