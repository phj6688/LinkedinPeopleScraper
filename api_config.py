import os
import secrets
from datetime import datetime, timedelta
import jwt

# API Configuration
API_PREFIX = '/api/v1'
API_DESCRIPTION = 'LinkedIn People Scraper API - Access LinkedIn scraping functionality programmatically'
API_VERSION = '1.0.0'

# Authentication settings
SECRET_KEY = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_DELTA = 24  # hours

# API key generation
def generate_api_key():
    """Generate a random API key"""
    return secrets.token_urlsafe(32)

# JWT token operations
def create_access_token(api_key):
    """Create a JWT token from an API key"""
    payload = {
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_DELTA),
        'iat': datetime.utcnow(),
        'api_key': api_key
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)

def decode_token(token):
    """Decode a JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload.get('api_key')
    except jwt.ExpiredSignatureError:
        return None  # Token has expired
    except jwt.InvalidTokenError:
        return None  # Invalid token