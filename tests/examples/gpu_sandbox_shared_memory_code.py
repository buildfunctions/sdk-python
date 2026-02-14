import sys
import json
import torch

def handler():
    """
    GPU Sandbox shared memory handler - reports GPU device count and memory info
    """
    try:
        cuda_available = torch.cuda.is_available()
        device_count = torch.cuda.device_count() if cuda_available else 0

        devices = []
        for i in range(device_count):
            name = torch.cuda.get_device_name(i)
            free, total = torch.cuda.mem_get_info(i)
            devices.append({
                "index": i,
                "name": name,
                "memory_total_mb": round(total / (1024 * 1024)),
                "memory_free_mb": round(free / (1024 * 1024)),
            })

        print(f"CUDA available: {cuda_available}, Device count: {device_count}")
        for d in devices:
            print(f"  Device {d['index']}: {d['name']} - {d['memory_total_mb']}MB total, {d['memory_free_mb']}MB free")

        response_data = {
            "message": "Hello from a Buildfunctions GPU Sandbox with shared memory!",
            "cuda_available": cuda_available,
            "device_count": device_count,
            "devices": devices,
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
