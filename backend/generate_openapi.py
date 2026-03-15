import os
import sys
import json
from flask import Flask

# Add root to path so we can import backend
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Skip dependency checks in main app
os.environ['FOSSILSAFE_SKIP_DEP_CHECK'] = '1'

try:
    from backend.lto_backend_main import app, initialize_app
except ImportError as e:
    print(f"Error importing app: {e}", file=sys.stderr)
    try:
        from lto_backend_main import app, initialize_app
    except ImportError:
        sys.exit(1)

def generate_openapi(app: Flask):
    paths = {}
    
    # Sort rules for consistent output
    rules = sorted(list(app.url_map.iter_rules()), key=lambda r: str(r))
    
    for rule in rules:
        if rule.rule.startswith("/static") or rule.rule.startswith("/_unused_static"):
            continue
        
        path = str(rule)
        # Convert Flask <param> format to OpenAPI {param} format if needed
        # But for now keeping as is for generated docs
        
        if path not in paths:
            paths[path] = {}
        
        for method in rule.methods:
            if method in ["HEAD", "OPTIONS"]:
                continue
                
            view_func = app.view_functions.get(rule.endpoint)
            docstring = view_func.__doc__ if view_func and view_func.__doc__ else "No description"
            summary = docstring.strip().split('\n')[0]
            description = docstring.strip()
            
            paths[path][method.lower()] = {
                "summary": summary,
                "description": description,
                "operationId": rule.endpoint,
                "responses": {
                    "200": {
                        "description": "Success"
                    }
                }
            }
            
    openapi = {
        "openapi": "3.0.0",
        "info": {
            "title": "FossilSafe API",
            "version": "1.0.0",
            "description": "Auto-generated API contract from backend code."
        },
        "paths": paths
    }
    
    return openapi

if __name__ == "__main__":
    from contextlib import redirect_stdout, redirect_stderr
    import io

    # Suppress output during initialization
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            initialize_app()
        except Exception:
            pass

    spec = generate_openapi(app)
    
    output_path = os.path.join(os.path.dirname(__file__), 'openapi.json')
    with open(output_path, 'w') as f:
        json.dump(spec, f, indent=2)
    
    print(f"Generated OpenAPI spec at {output_path}")
