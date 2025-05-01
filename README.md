# LinkedIn People Scraper

A modern Flask-based web application to scrape LinkedIn profiles from company pages based on keywords.

## Features

- **Modern Web Interface**: Clean, responsive UI built with Bootstrap 5
- **Company Selection**: Upload a CSV/TXT file containing LinkedIn company handles or enter them manually
- **Keyword Filtering**: Select from default keywords loaded from keywords.txt
- **Real-time Progress Tracking**: Live updates on scraping progress with detailed logs
- **Comprehensive Analytics**: Statistics on scraped data, most effective keywords, and top profiles
- **NLP Filtering**: Uses spaCy to filter results for genuine person names
- **Async Processing**: Background task processing to keep the UI responsive
- **CSV Export**: Results are saved as CSV and can be downloaded directly from the UI
- **Secure Credentials Handling**: LinkedIn credentials used only for active sessions, never stored permanently

## Requirements

- Python 3.8+
- Flask (Web framework)
- Playwright (Chromium browser automation)
- pandas (Data processing)
- spaCy (NLP for name filtering)
- See `requirements.txt` for all dependencies

## Installation

1. Clone this repository:
```bash
git clone https://github.com/yourusername/linkedin-people-scraper.git
cd linkedin-people-scraper
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Download the spaCy English model:
```bash
python -m spacy download en_core_web_sm
```

4. Install Playwright browsers:
```bash
playwright install chromium
```

## Project Setup in a New Folder

If you want to set up the project in a new folder for clarity:

```bash
# Create a new folder
mkdir linkedin-scraper
cd linkedin-scraper

# Copy essential files
cp /path/to/original/app.py .
cp /path/to/original/requirements.txt .
cp -r /path/to/original/templates .
cp -r /path/to/original/static .
cp /path/to/original/keywords.txt .

# Create necessary directories
mkdir uploads logs

# Install dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_sm
playwright install chromium
```

## Usage

1. Ensure your `keywords.txt` file is in the project directory with one keyword per line
2. Run the Flask application:
```bash
python app.py
```

3. Open your browser and go to http://127.0.0.1:5000
4. In the web interface:
   - Enter your LinkedIn credentials (used only for the current session)
   - Enter company handles or upload a CSV/TXT file
   - Select keywords from the list loaded from keywords.txt
   - Click "Start Scraping"
   - Monitor real-time progress and logs
   - Once completed, view statistics and download the CSV results

## Project Structure

- `app.py` - Main Flask application with scraper logic and API endpoints
- `templates/` - Directory containing HTML templates
  - `index.html` - Main UI template
- `static/` - Directory for static assets (CSS, JS, images)
- `keywords.txt` - Default keywords file
- `uploads/` - Directory for uploaded CSV/TXT files
- `logs/` - Directory for log files

## Notes

- The scraper uses semi-random delays to mimic human behavior
- LinkedIn may rate limit or block automated access; use responsibly
- Credentials are used only for the current session and not stored permanently

## License

MIT