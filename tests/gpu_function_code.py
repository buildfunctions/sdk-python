import sys
import json
import torch

def handler():
    """
    GPU Function handler
    """
    try:
        
        cuda_available = torch.cuda.is_available()
        device_count = torch.cuda.device_count() if cuda_available else 0
        device_name = torch.cuda.get_device_name(0) if cuda_available and device_count > 0 else "No GPU"

        print(f"Device set to: {device_name}")

        response_data = {
            "message": "Hello from a Buildfunctions GPU Function!",
            "cuda_available": cuda_available,
            "device_count": device_count,
            "device_name": device_name
        }

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
            },
            "body": json.dumps(response_data)
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
