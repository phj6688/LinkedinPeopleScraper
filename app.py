import os
import time
import json
import logging
import asyncio
import random
import threading
from pathlib import Path
from datetime import datetime
from functools import wraps

import pandas as pd
import spacy
from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
from werkzeug.utils import secure_filename
from playwright.async_api import async_playwright, Page, TimeoutError

# === CONFIG ===
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_change_in_production')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
app.config['ALLOWED_EXTENSIONS'] = {'csv', 'txt'}

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('logs', exist_ok=True)

# Setup logging
logging.basicConfig(
    filename="logs/scraper.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    filemode="w",
)
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

# Global variables for task management
active_tasks = {}
NLP = None

# Load spaCy model lazily
def get_nlp():
    global NLP
    if NLP is None:
        try:
            NLP = spacy.load("en_core_web_sm")
        except OSError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "spacy", "download", "en_core_web_sm"])
            NLP = spacy.load("en_core_web_sm")
    return NLP

# === UTILS ===
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def is_person_name(name_str):
    nlp = get_nlp()
    doc = nlp(name_str)
    return any(ent.label_ == "PERSON" for ent in doc.ents)

def load_keywords_from_file():
    try:
        with open("keywords.txt", "r") as f:
            return [kw.strip() for kw in f.readlines() if kw.strip()]
    except Exception as e:
        logger.error(f"Failed to load keywords: {e}")
        return []

async def _type_like_human(el, text: str):
    for ch in text:
        await el.type(ch)
        await asyncio.sleep(random.uniform(0.05, 0.2))

async def _handle_cookies_and_remove_selectors(page: Page):
    cookie_selectors = [
        "button[title='Accept cookies']",
        "button:has-text('Accept & continue')",
    ]
    remove_selectors = [".message-overlay", ".cookie-popup", "div[id*='notice']"]
    
    for sel in cookie_selectors:
        try:
            if await page.is_visible(sel, timeout=1_000):
                await page.click(sel)
                logger.info(f"Clicked consent selector: {sel}")
        except Exception:
            continue
    for sel in remove_selectors:
        try:
            await page.evaluate(f"""document.querySelectorAll("{sel}").forEach(e => e.remove())""")
        except Exception:
            pass

async def _full_page_scroll(page: Page):
    await page.evaluate("""
        async () => {
            await new Promise(resolve => {
                let total = 0, dist = 100;
                let timer = setInterval(() => {
                    window.scrollBy(0, dist);
                    total += dist;
                    if (total >= document.body.scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 100);
            });
        }
    """)

# Task management functions
def generate_task_id():
    return f"task_{int(time.time())}_{random.randint(1000, 9999)}"

def update_task_status(task_id, status, message=None, progress=None):
    if task_id in active_tasks:
        if status:
            active_tasks[task_id]['status'] = status
        if message:
            active_tasks[task_id]['logs'].append({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'message': message
            })
        if progress is not None:
            active_tasks[task_id]['progress'] = progress

# Scraping function adapted for web use
async def scrape_linkedin_profiles(task_id, companies, keywords, email, password, output_file):
    update_task_status(task_id, "running", "Starting LinkedIn scraper...")
    
    output_path = Path(output_file)
    output_path.unlink(missing_ok=True)
    
    total_steps = len(companies) * len(keywords) + 1  # +1 for login
    current_step = 0
    
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()

    try:
        # Login
        update_task_status(task_id, "running", "Logging in to LinkedIn...", (current_step / total_steps) * 100)
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await _handle_cookies_and_remove_selectors(page)
        await _type_like_human(page.locator('input[name="session_key"]'), email)
        await _type_like_human(page.locator('input[name="session_password"]'), password)
        await page.click('button[type="submit"]')
        await asyncio.sleep(5)  # Short wait after login
        
        current_step += 1
        update_task_status(task_id, "running", "Login successful", (current_step / total_steps) * 100)

        # Scraping loop
        for company in companies:
            for key in keywords:
                url = f"https://www.linkedin.com/company/{company}/people/?keywords={key}"
                try:
                    update_task_status(
                        task_id, 
                        "running", 
                        f"Scraping company: {company} with keyword: {key}",
                        (current_step / total_steps) * 100
                    )
                    
                    await page.goto(url)
                    await asyncio.sleep(random.uniform(2, 3))
                    
                    try:
                        await page.wait_for_selector("div.scaffold-finite-scroll__content ul a[href*='/in/']", timeout=15000)
                        await _full_page_scroll(page)
                        
                        pymk_ul = page.locator("div.scaffold-finite-scroll__content ul").first
                        profile_links = await pymk_ul.locator("a[href*='/in/']").evaluate_all("""
                            els => els.map(e => ({
                                href: e.href.split('?')[0],
                                name: e.textContent.trim().split('\\n')[0]
                            }))
                        """)
                        cleaned = [p for p in profile_links if p['name'] and p['href']]
                        
                        update_task_status(
                            task_id,
                            "running",
                            f"Found {len(cleaned)} profiles for {company} with keyword '{key}'",
                            (current_step / total_steps) * 100
                        )

                        if cleaned:
                            # Filter by real person names
                            filtered_data = [entry for entry in cleaned if is_person_name(entry['name'])]
                            if filtered_data:
                                df = pd.DataFrame(filtered_data)
                                df['company'] = company
                                df['keyword'] = key
                                df['url'] = url
                                df.to_csv(output_path, mode='a', header=not output_path.exists(), index=False)
                                
                                update_task_status(
                                    task_id,
                                    "running",
                                    f"Saved {len(filtered_data)} profiles for {company} with keyword '{key}'",
                                    (current_step / total_steps) * 100
                                )
                    except TimeoutError:
                        update_task_status(
                            task_id,
                            "running",
                            f"Timeout on {url} - No profiles found or page structure changed",
                            (current_step / total_steps) * 100
                        )
                except Exception as e:
                    update_task_status(
                        task_id,
                        "running",
                        f"Error on {url}: {str(e)}",
                        (current_step / total_steps) * 100
                    )
                
                current_step += 1
                
                # Add a small delay between requests
                await asyncio.sleep(random.uniform(3, 5))
                
        # Task completed successfully
        update_task_status(task_id, "completed", "Scraping completed successfully", 100)
        
    except Exception as e:
        update_task_status(task_id, "failed", f"Scraping failed: {str(e)}")
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()
        
        # Generate statistics
        if output_path.exists():
            try:
                df = pd.read_csv(output_path)
                stats = {
                    'total_companies': len(companies),
                    'total_keywords': len(keywords),
                    'total_profiles': len(df),
                    'unique_profiles': len(df['href'].unique()),
                }
                
                # Top profiles with most keyword matches
                top_profiles = df.groupby(['name', 'href']).size().reset_index(name='count').sort_values('count', ascending=False).head(5)
                top_profiles_list = top_profiles.to_dict('records')
                
                # Keyword effectiveness
                keyword_effectiveness = df.groupby('keyword').size().reset_index(name='count').sort_values('count', ascending=False)
                keyword_effectiveness_list = keyword_effectiveness.to_dict('records')
                
                # Top companies
                top_companies = df.groupby('company').size().reset_index(name='count').sort_values('count', ascending=False).head(5)
                top_companies_list = top_companies.to_dict('records')
                
                stats_message = f"""
                Statistics Summary:
                - Total Companies Searched: {stats['total_companies']}
                - Total Keywords Used: {stats['total_keywords']}
                - Total Profiles Found: {stats['total_profiles']}
                - Unique Profiles Found: {stats['unique_profiles']}
                """
                
                update_task_status(task_id, "completed", stats_message)
                active_tasks[task_id]['statistics'] = {
                    'basic_stats': stats,
                    'top_profiles': top_profiles_list,
                    'keyword_effectiveness': keyword_effectiveness_list,
                    'top_companies': top_companies_list
                }
            except Exception as e:
                update_task_status(task_id, "completed", f"Generated output file but failed to compute statistics: {str(e)}")

# === ROUTES ===
@app.route('/')
def index():
    keywords = load_keywords_from_file()
    return render_template('index.html', keywords=keywords)

@app.route('/start_scrape', methods=['POST'])
def start_scrape():
    # Get form data
    email = request.form.get('email')
    password = request.form.get('password')
    companies_input = request.form.get('companies')
    keywords = request.form.getlist('keywords')
    
    # Validate inputs
    if not email or not password:
        return jsonify({'status': 'error', 'message': 'LinkedIn credentials are required'}), 400
        
    if not keywords:
        return jsonify({'status': 'error', 'message': 'At least one keyword is required'}), 400
    
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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scrape_linkedin_profiles(task_id, companies, keywords, email, password, output_file))
        loop.close()
    
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
    
    if not os.path.exists(output_file):
        return jsonify({'status': 'error', 'message': 'Output file not found'}), 404
    
    return send_file(
        output_file,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"linkedin_profiles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

if __name__ == '__main__':
    host = '127.0.0.1'
    port = 5000
    url = f"http://{host}:{port}"
    print(f"\n✨ LinkedIn People Scraper is running!")
    print(f"🌐 Access the application at: {url}")
    print(f"📋 Press CTRL+C to quit\n")
    app.run(debug=True, host=host, port=port)
