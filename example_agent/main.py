"""Main handler for example agent."""

def handler(input_data):
    """Main agent handler."""
    return {
        "result": f"Processed: {input_data}",
        "agent": "example-three-surface-agent",
        "version": "0.1.0"
    }
