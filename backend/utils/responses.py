from flask import jsonify, g

def success_response(data=None, message=None, status_code=200):
    """
    Unified success response helper.
    Returns: {"success": True, "request_id": "...", "data": data, "message": message}
    """
    response = {
        "success": True,
        "request_id": getattr(g, "request_id", None)
    }
    if data is not None:
        response["data"] = data
    if message is not None:
        response["message"] = message
    return jsonify(response), status_code

def error_response(message="An internal error occurred", code="INTERNAL_ERROR", status_code=500, detail=None):
    """
    Unified error response helper.
    Returns: {"success": False, "request_id": "...", "error": {"code": code, "message": message, "detail": detail}}
    """
    response = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
        },
        "request_id": getattr(g, "request_id", None),
    }
    return jsonify(response), status_code
