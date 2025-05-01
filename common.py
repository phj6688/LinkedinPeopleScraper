import os
import time
import json
import logging
import asyncio
import random
from pathlib import Path
from datetime import datetime
import pandas as pd

# Common variables
active_tasks = {}
NLP = None

# Setup logging
logging.basicConfig(
    filename="logs/scraper.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    filemode="w",
)
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

# Load spaCy model lazily
def get_nlp():
    global NLP
    if NLP is None:
        try:
            import spacy
            NLP = spacy.load("en_core_web_sm")
        except OSError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "spacy", "download", "en_core_web_sm"])
            import spacy
            NLP = spacy.load("en_core_web_sm")
    return NLP

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

# Helper functions for scraper
async def _type_like_human(el, text: str):
    for ch in text:
        await el.type(ch)
        await asyncio.sleep(random.uniform(0.05, 0.2))

async def _handle_cookies_and_remove_selectors(page):
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

async def _full_page_scroll(page):
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

# Scraping function adapted for web use
async def scrape_linkedin_profiles(task_id, companies, keywords, email, password, output_file):
    from playwright.async_api import async_playwright, TimeoutError
    
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