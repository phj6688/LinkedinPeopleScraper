import os
import time
import json
import logging
import asyncio
import random
from pathlib import Path
from datetime import datetime
import pandas as pd
import traceback

os.makedirs("logs", exist_ok=True)
# Common variables
active_tasks = {}
NLP = None
PEOPLE_SELECTOR = 'div.artdeco-entity-lockup__title a[href*="/in/"]'
# Setup logging
logging.basicConfig(
    filename="logs/scraper.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    filemode="a",
)
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

# Load spaCy model lazily
def get_nlp():
    """Lazily load the spaCy model."""
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
    """Return True if the string is recognized as a person name by spaCy."""
    nlp = get_nlp()
    doc = nlp(name_str)
    return any(ent.label_ == "PERSON" for ent in doc.ents)

def load_keywords_from_file():
    """Load keywords from keywords.txt, one per line."""
    try:
        with open("keywords.txt", "r") as f:
            return [kw.strip() for kw in f.readlines() if kw.strip()]
    except Exception as e:
        logger.error(f"Failed to load keywords: {e}")
        return []

# Task management functions
def generate_task_id():
    """Generate a unique task ID."""
    return f"task_{int(time.time())}_{random.randint(1000, 9999)}"

def update_task_status(task_id, status, message=None, progress=None):
    """Update the status, logs, and progress of a scraping task."""
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
    """Main scraping coroutine for LinkedIn profiles."""
    from playwright.async_api import async_playwright, TimeoutError
    
    logger.info(f"[SCRAPER] scrape_linkedin_profiles called for task_id={task_id}, companies={companies}, keywords={keywords}, output_file={output_file}")
    update_task_status(task_id, "running", "Starting LinkedIn scraper...")
    
    output_path = Path(output_file)
    output_path.unlink(missing_ok=True)
    
    total_steps = len(companies) * len(keywords) + 1  # +1 for login
    current_step = 0
    
    # --- Playwright context setup to match working script ---
    user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113 Safari/537.36"
    )
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=[
        "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"
    ])
    context = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1280, "height": 800}
    )
    page = await context.new_page()

    try:
        # Login
        update_task_status(task_id, "running", "Logging in to LinkedIn...", (current_step / total_steps) * 100)
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await _handle_cookies_and_remove_selectors(page)
        await _type_like_human(page.locator('input[name="session_key"]'), email)
        await _type_like_human(page.locator('input[name="session_password"]'), password)
        await page.click('button[type="submit"]')
        await asyncio.sleep(2)
        # Wait for either profile photo (success) or login form/challenge (failure)
        login_success = False
        try:
            await page.wait_for_selector('img.global-nav__me-photo', timeout=10000)
            login_success = True
        except Exception:
            pass
        if not login_success:
            # Check for login form still visible
            if await page.is_visible('input[name="session_key"]', timeout=2000):
                update_task_status(task_id, "failed", "❌ Login failed: Please check your LinkedIn credentials or if your account requires additional verification.", (current_step / total_steps) * 100)
                await context.close()
                await browser.close()
                await playwright.stop()
                return
            # Check for challenge
            if await page.is_visible('form[action*="checkpoint/challenge"]', timeout=2000):
                update_task_status(task_id, "failed", "❌ Login challenge detected: LinkedIn is asking for additional verification (e.g., CAPTCHA, email, or phone verification). Please resolve this on your account and try again.", (current_step / total_steps) * 100)
                await context.close()
                await browser.close()
                await playwright.stop()
                return
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
                    await page.goto(url, wait_until="domcontentloaded")
                    await asyncio.sleep(random.uniform(2, 3))
                    await _handle_cookies_and_remove_selectors(page)
                    await _full_page_scroll(page)
                    # Wait for a general container, not the profile link itself
                    try:
                        await page.wait_for_selector(PEOPLE_SELECTOR, timeout=15000)
                        

                    except TimeoutError:
                        update_task_status(
                            task_id,
                            "running",
                            f"No people section found for {company} with keyword '{key}' (skipping)",
                            (current_step / total_steps) * 100
                        )
                        continue
                    await _handle_cookies_and_remove_selectors(page)
                    await _full_page_scroll(page)                     
                    await page.wait_for_selector(PEOPLE_SELECTOR, timeout=15_000)  

                    profile_links = await page.eval_on_selector_all(
                        PEOPLE_SELECTOR,
                        'els => els.map(e => ({href: e.href.split("?")[0], name: e.textContent.trim()}))'
                    )
                    cleaned = [p for p in profile_links if p['name'] and p['href']]
                    update_task_status(
                        task_id,
                        "running",
                        f"Found {len(cleaned)} profiles for {company} with keyword '{key}'",
                        (current_step / total_steps) * 100
                    )
                    if cleaned:
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
                except Exception as e:
                    logger.error(f"Error scraping {url}: {e}")
                    update_task_status(
                        task_id,
                        "running",
                        f"Error on {url}: {str(e)}",
                        (current_step / total_steps) * 100
                    )
                current_step += 1
                await asyncio.sleep(random.uniform(3, 5))
        
        # Task completed successfully
        update_task_status(task_id, "completed", "Scraping completed successfully", 100)
        
    except Exception as e:
        logger.error(f"Scraping failed: {e}\n{traceback.format_exc()}")
        update_task_status(task_id, "failed", f"Scraping failed: {str(e)}")
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()
        
        # Generate statistics
        if output_path.exists():
            try:
                df = pd.read_csv(output_path)
                if df.empty:
                    stats = {
                        'total_companies': len(companies),
                        'total_keywords': len(keywords),
                        'total_profiles': 0,
                        'unique_profiles': 0,
                    }
                    active_tasks[task_id]['statistics'] = {
                        'basic_stats': stats,
                        'top_profiles': [],
                        'keyword_effectiveness': [],
                        'top_companies': []
                    }
                    update_task_status(task_id, "completed", "No profiles found.")
                else:
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
                logger.error(f"Failed to compute statistics: {e}")
                update_task_status(task_id, "completed", f"Generated output file but failed to compute statistics: {str(e)}")
        else:
            # Output file does not exist at all
            stats = {
                'total_companies': len(companies),
                'total_keywords': len(keywords),
                'total_profiles': 0,
                'unique_profiles': 0,
            }
            active_tasks[task_id]['statistics'] = {
                'basic_stats': stats,
                'top_profiles': [],
                'keyword_effectiveness': [],
                'top_companies': []
            }
            update_task_status(task_id, "completed", "No profiles found.")