import functools
import os
from flask import Blueprint, request, jsonify, current_app
from flask_cors import cross_origin
import json
import threading
import asyncio

from api_keys import validate_api_key, create_api_key, delete_api_key, list_api_keys
from api_config import API_PREFIX, decode_token, create_access_token, API_VERSION, API_DESCRIPTION

# Import from common module instead of app
from common import (
    scrape_linkedin_profiles, generate_task_id, active_tasks, 
    update_task_status, load_keywords_from_file
)

# Create API blueprint
api_bp = Blueprint('api', __name__, url_prefix=API_PREFIX)

# Authentication decorator
def require_api_key(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for API key in header
        auth_header = request.headers.get('Authorization')
        if auth_header:
            try:
                # Extract the token (Bearer format)
                auth_type, token = auth_header.split(None, 1)
                if auth_type.lower() == 'bearer':
                    # Extract the API key from the token
                    api_key = decode_token(token)
                    if api_key and validate_api_key(api_key):
                        return f(*args, **kwargs)
            except ValueError:
                pass
                
        # Check for API key in query parameters
        api_key = request.args.get('api_key')
        if api_key and validate_api_key(api_key):
            return f(*args, **kwargs)
                
        return jsonify({'status': 'error', 'message': 'Invalid or missing API key'}), 401
    return decorated_function

# API Status endpoint (public)
@api_bp.route('/status', methods=['GET'])
@cross_origin()
def api_status():
    return jsonify({
        'status': 'online',
        'version': API_VERSION,
        'description': API_DESCRIPTION
    })

# Authentication endpoints
@api_bp.route('/auth/token', methods=['POST'])
@cross_origin()
def get_token():
    data = request.get_json()
    api_key = data.get('api_key') if data else None
    
    if not api_key:
        return jsonify({'status': 'error', 'message': 'API key is required'}), 400
    
    if not validate_api_key(api_key):
        return jsonify({'status': 'error', 'message': 'Invalid API key'}), 401
    
    token = create_access_token(api_key)
    return jsonify({
        'status': 'success',
        'access_token': token,
        'token_type': 'bearer'
    })

# Admin API endpoints
@api_bp.route('/admin/keys', methods=['GET'])
@require_api_key
def list_keys():
    keys = list_api_keys()
    return jsonify({'status': 'success', 'keys': keys})

@api_bp.route('/admin/keys', methods=['POST'])
@require_api_key
def create_key():
    data = request.get_json()
    name = data.get('name', 'API Key')
    description = data.get('description', '')
    
    new_key = create_api_key(name, description)
    return jsonify({
        'status': 'success',
        'api_key': new_key,
        'name': name,
        'description': description
    })

@api_bp.route('/admin/keys/<api_key>', methods=['DELETE'])
@require_api_key
def delete_key(api_key):
    result = delete_api_key(api_key)
    if result:
        return jsonify({'status': 'success', 'message': 'API key deleted'})
    return jsonify({'status': 'error', 'message': 'API key not found'}), 404

# Scraper API endpoints
@api_bp.route('/keywords', methods=['GET'])
@cross_origin()
@require_api_key
def get_keywords():
    keywords = load_keywords_from_file()
    return jsonify({
        'status': 'success',
        'keywords': keywords
    })

@api_bp.route('/tasks', methods=['POST'])
@cross_origin()
@require_api_key
def start_task():
    data = request.get_json()
    
    # Validate required data
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400
    
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({'status': 'error', 'message': 'LinkedIn credentials are required'}), 400
    
    # Process companies from JSON data
    companies = data.get('companies', [])
    if isinstance(companies, str):
        companies = [c.strip() for c in companies.split(',') if c.strip()]
    
    if not companies:
        return jsonify({'status': 'error', 'message': 'No companies provided'}), 400
    
    # Process keywords - enhanced handling for different formats
    keywords = data.get('keywords', [])
    
    # Handle string input (comma-separated)
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(',') if k.strip()]
    # Handle list with comma-separated strings
    elif isinstance(keywords, list):
        processed_keywords = []
        for k in keywords:
            if isinstance(k, str) and ',' in k:
                # Split and add each part as a separate keyword
                processed_keywords.extend([part.strip() for part in k.split(',') if part.strip()])
            elif k:  # Only add non-empty keywords
                processed_keywords.append(k)
        keywords = processed_keywords
    
    if not keywords:
        return jsonify({'status': 'error', 'message': 'At least one keyword is required'}), 400
    
    # Log the processed keywords for debugging
    current_app.logger.info(f"API task starting with keywords: {keywords}")
    
    # Create task
    task_id = generate_task_id()
    output_file = f"output_{task_id}.csv"
    
    # Initialize task
    active_tasks[task_id] = {
        'status': 'pending',
        'progress': 0,
        'logs': [],
        'output_file': output_file,
        'statistics': None
    }
    
    # Start the scraping task in the background
    def run_async_task():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scrape_linkedin_profiles(task_id, companies, keywords, email, password, output_file))
        loop.close()
    
    thread = threading.Thread(target=run_async_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'status': 'success',
        'message': 'Task started successfully',
        'task_id': task_id
    })

@api_bp.route('/tasks/<task_id>', methods=['GET'])
@cross_origin()
@require_api_key
def get_task_status(task_id):
    if task_id not in active_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    return jsonify({
        'status': active_tasks[task_id]['status'],
        'progress': active_tasks[task_id]['progress'],
        'logs': active_tasks[task_id]['logs'],
        'statistics': active_tasks[task_id]['statistics'] if 'statistics' in active_tasks[task_id] else None
    })

@api_bp.route('/tasks/<task_id>/results', methods=['GET'])
@cross_origin()
@require_api_key
def get_task_results(task_id):
    if task_id not in active_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    output_file = active_tasks[task_id]['output_file']
    
    if not os.path.exists(output_file):
        return jsonify({'status': 'error', 'message': 'Output file not found'}), 404
    
    # Read the CSV file and return as JSON
    import pandas as pd
    try:
        df = pd.read_csv(output_file)
        results = df.to_dict('records')
        return jsonify({
            'status': 'success',
            'count': len(results),
            'results': results
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error reading results: {str(e)}'}), 500

@api_bp.route('/tasks/<task_id>/download', methods=['GET'])
@cross_origin()
@require_api_key
def download_task_results(task_id):
    if task_id not in active_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    output_file = active_tasks[task_id]['output_file']
    
    if not os.path.exists(output_file):
        return jsonify({'status': 'error', 'message': 'Output file not found'}), 404
    
    from flask import send_file
    from datetime import datetime
    
    return send_file(
        output_file,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"linkedin_profiles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )