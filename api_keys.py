import os
import json
from pathlib import Path
from api_config import generate_api_key

# Path to store API keys
API_KEYS_FILE = 'api_keys.json'

def load_api_keys():
    """Load API keys from file"""
    try:
        if os.path.exists(API_KEYS_FILE):
            with open(API_KEYS_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception:
        return {}

def save_api_keys(keys_dict):
    """Save API keys to file"""
    with open(API_KEYS_FILE, 'w') as f:
        json.dump(keys_dict, f)

def create_api_key(name, description=""):
    """Create a new API key"""
    keys = load_api_keys()
    new_key = generate_api_key()
    
    keys[new_key] = {
        "name": name,
        "description": description,
        "created_at": str(Path(API_KEYS_FILE).stat().st_mtime if os.path.exists(API_KEYS_FILE) else 0)
    }
    
    save_api_keys(keys)
    return new_key

def validate_api_key(key):
    """Check if an API key is valid"""
    keys = load_api_keys()
    return key in keys

def get_key_details(key):
    """Get details for an API key"""
    keys = load_api_keys()
    return keys.get(key, {})

def delete_api_key(key):
    """Delete an API key"""
    keys = load_api_keys()
    if key in keys:
        del keys[key]
        save_api_keys(keys)
        return True
    return False

def list_api_keys():
    """List all API keys without exposing the actual keys"""
    keys = load_api_keys()
    result = []
    
    for key, details in keys.items():
        # Only show the first 8 characters of the key for security
        masked_key = f"{key[:8]}{'*' * (len(key) - 8)}"
        details['masked_key'] = masked_key
        result.append(details)
        
    return result