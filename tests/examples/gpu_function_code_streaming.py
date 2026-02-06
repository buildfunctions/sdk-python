import sys
import json
import time
import torch

# Simulated AI response to stream
MOCK_RESPONSE = (
    "The most mysterious phenomenon in the universe is dark energy, "
    "an invisible force that makes up a large amount of the cosmos yet remains completely undetectable by direct observation. "
    "Scientists know it exists only because the universe is expanding at an accelerating rate, defying gravitational expectations. "
    "What makes it truly enigmatic is that after decades of research, we still have no idea what it actually is or where it comes from."
)

async def stream_mock_response():
    """
    Streams the mock AI response word by word to simulate real-time text generation.
    """
    try:
        yield b"<<START_STREAM>>\n"
        
        words = MOCK_RESPONSE.split()
        
        for i, word in enumerate(words):
            # Add space before word (except for first word)
            token = f" {word}" if i > 0 else word
            yield f"<<STREAM_CHUNK>>{token}<<END_STREAM_CHUNK>>\n".encode()
            
            # Small delay to simulate generation time
            time.sleep(0.05)
        
        yield b"<<END_STREAM>>\n"
        
    except Exception as e:
        print(f"Error in streaming: {e}", file=sys.stderr)
        yield b"<<STREAM_ERROR>>\n"

async def async_stream_wrapper():
    """
    Wraps the streaming process for async iteration.
    """
    try:
        async for chunk in stream_mock_response():
            yield chunk
    except Exception as e:
        print(f"Error in async stream wrapper: {e}", file=sys.stderr)
        yield b"<<STREAM_ERROR>>\n"

def handler():
    """
    GPU Function handler with streaming response.
    """
    try:
        cuda_available = torch.cuda.is_available()
        device_count = torch.cuda.device_count() if cuda_available else 0
        device_name = torch.cuda.get_device_name(0) if cuda_available and device_count > 0 else "No GPU"

        print(f"Device set to: {device_name}")
        print(f"CUDA available: {cuda_available}", file=sys.stderr)
        print(f"Starting streaming response...", file=sys.stderr)

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "X-Device-Name": device_name,
                "X-CUDA-Available": str(cuda_available),
            },
            "body": async_stream_wrapper(),
        }

    except Exception as e:
        print(f"Error in handler: {e}", file=sys.stderr)
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
            },
            "body": json.dumps({"error": str(e)})
        }
