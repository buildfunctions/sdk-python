def handler(event, context):

    response = {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/plain'},
        'body': f'Hello from a Buildfunctions CPU Function!'
    }

    return response
