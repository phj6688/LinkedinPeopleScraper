import os
import threading
import asyncio
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple
import traceback
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

# Import shared functionality from common module
from common import (
    active_tasks, update_task_status, load_keywords_from_file,
    scrape_linkedin_profiles, generate_task_id, logger,
    is_person_name
)

# === CONFIG ===
app = Flask(__name__)

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_change_in_production')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
app.config['ALLOWED_EXTENSIONS'] = {'csv', 'txt'}
app.config['APPLICATION_ROOT'] = '/linkedinpeoplescraper'

# Enable CORS
CORS(app)

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('logs', exist_ok=True)

# Improved logging with rotation
handler = RotatingFileHandler('logs/scraper.log', maxBytes=2*1024*1024, backupCount=5)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
handler.setFormatter(formatter)
if not app.logger.handlers:
    app.logger.addHandler(handler)

RATE_LIMIT_SECONDS = 60
last_scrape_time = {}

# === UTILS ===
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# === ROUTES ===
@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Exception: {str(e)}\n{traceback.format_exc()}")
    response = {
        'status': 'error',
        'message': str(e),
        'trace': traceback.format_exc()
    }
    return jsonify(response), 500

@app.before_request
def log_request_info():
    app.logger.info(f"Incoming request: {request.method} {request.path} | Args: {request.args} | Form: {request.form}")

@app.before_request
def detect_script_name():
    script_name = request.headers.get('X-Forwarded-Prefix')
    if script_name:
        request.environ['SCRIPT_NAME'] = script_name

@app.route('/')
def index():
    keywords = load_keywords_from_file()
    return render_template('index.html', keywords=keywords)

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    user_ip = request.remote_addr
    now = datetime.utcnow()
    if user_ip in last_scrape_time and (now - last_scrape_time[user_ip]).total_seconds() < RATE_LIMIT_SECONDS:
        return jsonify({'status': 'error', 'message': f'Rate limit: Please wait {RATE_LIMIT_SECONDS} seconds between scrapes.'}), 429
    last_scrape_time[user_ip] = now

    # Get form data
    email = request.form.get('email')
    password = request.form.get('password')
    companies_input = request.form.get('companies')
<<<<<<< HEAD
    
    # Process keywords with enhanced handling
    keywords_input = request.form.getlist('keywords')
    
    # Handle multiple formats of keywords
    keywords = []
    for k in keywords_input:
        if ',' in k:
            # Split comma-separated values
            keywords.extend([part.strip() for part in k.split(',') if part.strip()])
        elif k.strip():
            # Add single keywords
            keywords.append(k.strip())
=======
    keywords = request.form.get('keywords', '').split(',') if request.form.get('keywords') else []
>>>>>>> dev/feat/api
    
    # Validate inputs
    if not email or not password:
        return jsonify({'status': 'error', 'message': 'LinkedIn credentials are required'}), 400
        
    if not keywords:
        return jsonify({'status': 'error', 'message': 'At least one keyword is required'}), 400
    
    # Log the processed keywords for debugging
    app.logger.info(f"Web task starting with keywords: {keywords}")
    
    # Process companies input (either direct input or file upload)
    companies = []
    if companies_input:
        # Process text input
        companies = [c.strip() for c in companies_input.split(',') if c.strip()]
    elif 'company_file' in request.files:
        file = request.files['company_file']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Process uploaded file based on extension
            if filename.endswith('.csv'):
                try:
                    df = pd.read_csv(file_path)
                    if 'linkedin_company_name' in df.columns:
                        companies = df['linkedin_company_name'].dropna().unique().tolist()
                    else:
                        # Try to use the first column
                        companies = df.iloc[:, 0].dropna().unique().tolist()
                except Exception as e:
                    return jsonify({'status': 'error', 'message': f'Error processing CSV: {str(e)}'}), 400
            elif filename.endswith('.txt'):
                with open(file_path, 'r') as f:
                    companies = [line.strip() for line in f if line.strip()]
    
    if not companies:
        return jsonify({'status': 'error', 'message': 'No companies provided'}), 400
    
    # Clean up old files (uploads and outputs older than 1 day)
    cutoff = datetime.utcnow() - timedelta(days=1)
    for folder in [app.config['UPLOAD_FOLDER'], '.']:
        for fname in os.listdir(folder):
            if fname.startswith('output_') or fname.endswith('.csv') or fname.endswith('.txt'):
                fpath = os.path.join(folder, fname)
                try:
                    mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
                    if mtime < cutoff:
                        os.remove(fpath)
                except Exception:
                    pass

    # Create a task ID and setup the task
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
    
    # Start task in a separate thread
    def run_async_task():
        try:
            logger.info(f"[APP] Starting async scrape task for task_id={task_id}")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(scrape_linkedin_profiles(task_id, companies, keywords, email, password, output_file))
            loop.close()
        except Exception as e:
            logger.error(f"[APP] Exception in run_async_task for task_id={task_id}: {e}\n{traceback.format_exc()}")
            update_task_status(task_id, "failed", f"Internal error: {str(e)}")

    logger.info(f"[APP] Launching thread for task_id={task_id}")
    thread = threading.Thread(target=run_async_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'status': 'success', 
        'message': 'Scraping task started',
        'task_id': task_id
    })
@app.route('/task_status/<task_id>', methods=['GET'])
def task_status(task_id):
    if task_id not in active_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    return jsonify({
        'status': active_tasks[task_id]['status'],
        'progress': active_tasks[task_id]['progress'],
        'logs': active_tasks[task_id]['logs'],
        'statistics': active_tasks[task_id]['statistics'] if 'statistics' in active_tasks[task_id] else None
    })

@app.route('/download/<task_id>', methods=['GET'])
def download_results(task_id):
    if task_id not in active_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    output_file = active_tasks[task_id]['output_file']
    abs_output_file = os.path.abspath(output_file)
    app.logger.info(f"[DOWNLOAD] Sending file: {abs_output_file}")
    if not os.path.exists(abs_output_file):
        return jsonify({'status': 'error', 'message': 'Output file not found'}), 404
    return send_file(
        abs_output_file,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"linkedin_profiles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

# Register API blueprint
from api_routes import api_bp
app.register_blueprint(api_bp)

# Mount at /linkedinpeoplescraper using DispatcherMiddleware
application = DispatcherMiddleware(Flask('dummy'), {
    '/linkedinpeoplescraper': app
})

if __name__ == '__main__':
    # Generate a default API key if none exists
    from api_keys import load_api_keys, create_api_key
    keys = load_api_keys()
    if not keys:
        default_key = create_api_key("Default API Key", "Created on first startup")
        print(f"\n🔑 Default API Key: {default_key}")
        print(f"Store this key securely. It will be required to access the API.\n")
    
<<<<<<< HEAD
    # Start the Flask application
    host = '0.0.0.0'
    port = 5001
    url = f"http://{host}:{port}"
=======
    # Start the Flask application with DispatcherMiddleware
    host = '0.0.0.0'
    port = 5000
>>>>>>> dev/feat/api
    print(f"\n✨ LinkedIn People Scraper is running!")
    print(f"🌐 Access the application at: https://apps.peyman.io/linkedinpeoplescraper/")
    print(f"🔌 API is available at: https://apps.peyman.io/linkedinpeoplescraper/api/v1/status")
    print(f"📋 Press CTRL+C to quit\n")
    run_simple(host, port, application)