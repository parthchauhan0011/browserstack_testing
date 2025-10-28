import os
import time
import re
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.safari.options import Options as SafariOptions

# Google Translate client
try:
    from google.cloud import translate_v3 as translate
    HAS_GOOGLE = True
except Exception:
    HAS_GOOGLE = False

# ---------- Helpers ----------
BASE_DIR = Path(__file__).parent if '__file__' in locals() else Path.cwd()
IMAGES_DIR = BASE_DIR / 'images'
IMAGES_DIR.mkdir(exist_ok=True)

ELPAIS_OPINION = 'https://elpais.com/opinion/'


def ensure_spanish_chrome_options():
    opts = ChromeOptions()
    # Force UI and Accept-Language to Spanish
    opts.add_argument('--lang=es')
    opts.add_experimental_option('prefs', {'intl.accept_languages': 'es,es_ES'})
    # headful by default so you can see it; uncomment for headless
    # opts.add_argument('--headless=new')
    return opts


def get_local_driver(browser='chrome'):
    if browser == 'chrome':
        opts = ensure_spanish_chrome_options()
        driver = webdriver.Chrome(options=opts)
        return driver
    elif browser == 'firefox':
        opts = FirefoxOptions()
        opts.set_preference('intl.accept_languages', 'es-ES, es')
        driver = webdriver.Firefox(options=opts)
        return driver
    else:
        raise ValueError('Unsupported browser for local run')


# ---------- Scraping logic ----------

def scrape_opinion_articles(driver, max_articles=5):
    """Given an open webdriver on the Opinion landing, return a list of dicts with
    title (spanish), url, body (spanish), cover_image_path (if downloaded), translated_title (placeholder).
    """
    print(f"[Thread: {threading.current_thread().name}] Opening {ELPAIS_OPINION}...")
    driver.get(ELPAIS_OPINION)
    # Let page load and handle any cookie banners if necessary
    time.sleep(2) 

    soup = BeautifulSoup(driver.page_source, 'html.parser')

    # Find article links
    candidates = []
    # Look for links inside <h2> or <h3> tags, a common pattern for headlines
    for a in soup.select('h2 a[href], h3 a[href]'):
        href = a.get('href')
        if not href:
            continue

        full = href
        if href.startswith('/'):
            full = urljoin('https://elpais.com', href)
        
        # Filter for opinion articles (must be on elpais.com, contain /opinion/, and look like an article page)
        if 'elpais.com' in full and '/opinion/' in full and full.endswith('.html'):
            candidates.append(full)

    # dedupe while preserving order
    seen = set()
    ordered = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
            
    article_urls = ordered[:max_articles]

    if not article_urls:
        print(f"WARNING: [Thread: {threading.current_thread().name}] No article URLs found. Page structure may have changed.")
    else:
        print(f"[Thread: {threading.current_thread().name}] Found {len(article_urls)} article URLs to scrape.")


    results = []
    for url in article_urls:
        try:
            print(f"[Thread: {threading.current_thread().name}] Scraping {url}...")
            driver.get(url)
            time.sleep(1.5)
            page = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Title
            title_tag = page.find(['h1'])
            title = title_tag.get_text(strip=True) if title_tag else 'No title found'
            
            # Body: many articles have <div class="article_body"> or <div itemprop="articleBody">
            body = ''
            body_container = page.find(attrs={'itemprop': 'articleBody'}) or page.find(class_=re.compile('article_body|articulo|cuerpo'))
            if body_container:
                paras = [p.get_text(strip=True) for p in body_container.find_all('p')]
                body = '\n\n'.join([p for p in paras if p])
            else:
                # fallback: collect all <p> inside main
                main_tag = page.find('main')
                if main_tag:
                    paras = [p.get_text(strip=True) for p in main_tag.find_all('p')]
                    body = '\n\n'.join(paras[:40]) # Limit fallback
                else:
                    body = "No article body container found."


            # Cover image: common patterns: figure img, meta property="og:image"
            cover_path = None
            img_url = None
            
            # Best method: OpenGraph meta tag
            og = page.find('meta', property='og:image')
            if og and og.get('content'):
                img_url = og.get('content')
            else:
                # Fallback: Try to find the first <figure> and get an <img> from it
                fig_tag = page.find('figure') 
                if fig_tag:
                    img_tag = fig_tag.find('img')
                    if img_tag:
                        # Check src, or data-src for lazy loading
                        img_url = img_tag.get('src') or img_tag.get('data-src')

            if img_url:
                try:
                    # Ensure URL is absolute
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    elif img_url.startswith('/'):
                        img_url = urljoin('https://elpais.com', img_url)

                    r = requests.get(img_url, timeout=15)
                    if r.status_code == 200:
                        # Clean the filename: get last part of URL, remove .html, keep first 50 chars
                        url_slug = url.split('/')[-1].replace('.html', '')[:50]
                        
                        # Try to guess extension, default to .jpg
                        content_type = r.headers.get('content-type')
                        ext = '.jpg' # default
                        if content_type:
                            if 'jpeg' in content_type: ext = '.jpg'
                            elif 'png' in content_type: ext = '.png'
                            elif 'gif' in content_type: ext = '.gif'
                            elif 'webp' in content_type: ext = '.webp'

                        fname = IMAGES_DIR / (url_slug + ext)
                        with open(fname, 'wb') as f:
                            f.write(r.content)
                        cover_path = str(fname)
                except Exception as e:
                    print('Image download failed for', img_url, e)

            results.append({'url': url, 'title_es': title, 'body_es': body, 'cover_image': cover_path, 'title_en': None})
        except Exception as e:
            print(f'Failed to scrape {url}: {e}')
    return results


# ---------- Translation ----------

def translate_texts_google(texts, target='en'):
    if not HAS_GOOGLE:
        raise RuntimeError('google-cloud-translate library not available. Install google-cloud-translate or implement alternate API.')
    
    client = translate.TranslationServiceClient()
    project_id = os.environ.get('GOOGLE_CLOUD_PROJECT') or os.environ.get('GCP_PROJECT')

    if not project_id:
        # Try to load from credentials file if env var is not set
        cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if cred_path:
            try:
                with open(cred_path, 'r') as f:
                    creds = json.load(f)
                project_id = creds.get('project_id')
            except Exception as e:
                print(f"Could not read project_id from {cred_path}: {e}")

    if not project_id:
        raise RuntimeError(
            'Could not determine Google Cloud Project ID. '
            'Set GOOGLE_CLOUD_PROJECT env var or ensure "project_id" is in your GOOGLE_APPLICATION_CREDENTIALS JSON.'
        )
        
    parent = f'projects/{project_id}/locations/global'
    responses = []
    
    for text in texts:
        response = client.translate_text(request={
            'parent': parent,
            'contents': [text],
            'mime_type': 'text/plain',
            'target_language_code': target,
            'source_language_code': 'es' # Be explicit
        })
        # response.translations is a list
        translations = [t.translated_text for t in response.translations]
        responses.append(translations[0] if translations else '')
    return responses


# ---------- Analysis ----------

def analyze_translated_headers(translated_titles):
    # split into words, normalize: lower, strip punctuation
    words = []
    for t in translated_titles:
        if not t: continue
        # remove punctuation and split
        cleaned = re.sub(r"[^\w\s]", ' ', t)
        for w in cleaned.lower().split():
            # filter out very short common words
            if len(w) > 2:
                words.append(w)
                
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
        
    repeated = {w: c for w, c in counts.items() if c > 2}
    return repeated


# ---------- BrowserStack Remote Driver ----------

# ---------- BrowserStack Remote Driver ----------

# ---------- BrowserStack Remote Driver ----------

def get_browserstack_driver(cap: dict):
    user = os.environ.get('BROWSERSTACK_USERNAME')
    key = os.environ.get('BROWSERSTACK_ACCESS_KEY')
    if not user or not key:
        raise RuntimeError('Set BROWSERSTACK_USERNAME and BROWSERSTACK_ACCESS_KEY')

    # Use the hub URL *without* credentials
    url = 'https://hub-cloud.browserstack.com/wd/hub'

    # Get browserName to instantiate the correct Options object
    browser_name = cap.get('browserName', '').lower()

    if browser_name == 'chrome':
        options = ChromeOptions()
        options.add_argument('--lang=es')
        options.add_experimental_option('prefs', {'intl.accept_languages': 'es,es_ES'})
    elif browser_name == 'firefox':
        options = FirefoxOptions()
        options.set_preference('intl.accept_languages', 'es-ES, es')
    elif browser_name == 'edge':
        options = EdgeOptions()
        options.add_argument('--lang=es')
        options.add_experimental_option('prefs', {'intl.accept_languages': 'es,es_ES'})
    elif browser_name == 'safari':
        options = SafariOptions()
    else:
        # Fallback for mobile (which uses browserName 'Safari' or 'Chrome')
        # or other types.
        options = ChromeOptions()

    # --- THIS IS THE FIX ---
    # 1. Get the bstack:options from the input 'cap' dictionary.
    #    If it doesn't exist (which it should), create an empty one.
    bstack_options = cap.get('bstack:options', {})

    # 2. Add authentication keys to it.
    bstack_options['userName'] = user
    bstack_options['accessKey'] = key

    # 3. Put the modified bstack_options back into the main 'cap' dict.
    cap['bstack:options'] = bstack_options
    
    # 4. Now, set all capabilities from the *modified* 'cap' dict onto the options object.
    for key, value in cap.items():
        options.set_capability(key, value)
    
    # The previous buggy code (options.get_capability) is now gone.

    return webdriver.Remote(command_executor=url, options=options)

# ---------- Orchestration ----------

def run_full_flow_local():
    driver = None
    try:
        driver = get_local_driver('chrome')
        results = scrape_opinion_articles(driver, max_articles=5)
    except Exception as e:
        print(f"Local run failed during scraping: {e}")
        return
    finally:
        if driver:
            driver.quit()

    if not results:
        print("No results found, exiting.")
        return

    # Translate titles using Google Cloud Translate (example)
    titles = [r['title_es'] for r in results]
    translated = []
    if HAS_GOOGLE:
        try:
            print("Translating titles...")
            translated = translate_texts_google(titles, target='en')
        except Exception as e:
            print('Google Translate failed:', e)
            translated = ['(translation failed)'] * len(titles)
    else:
        print("Google Translate library not found. Skipping translation.")
        translated = ['(google lib missing)'] * len(titles)

    for i, r in enumerate(results):
        r['title_en'] = translated[i]

    # Print outputs
    for r in results:
        print('\n--- ARTICLE ---')
        print('URL:', r['url'])
        print('Title (ES):', r['title_es'])
        print('Title (EN):', r['title_en'])
        print('\nBody (ES):\n', (r['body_es'][:1000] + '...') if r['body_es'] and len(r['body_es'])>1000 else r['body_es'])
        print('Cover image saved at:', r['cover_image'])

    repeated = analyze_translated_headers([r['title_en'] for r in results])
    print('\nWords repeated more than twice in all translated headers:')
    if repeated:
        for w, c in repeated.items():
            print(f"'{w}': {c}")
    else:
        print("(None)")


# ---------- Example BrowserStack parallel run ----------

def browserstack_worker(cap):
    driver = get_browserstack_driver(cap)
    try:
        res = scrape_opinion_articles(driver, max_articles=5)
        return res
    finally:
        try:
            driver.quit()
        except Exception:
            pass # Ignore quit errors


def run_on_browserstack_parallel(capabilities_list):
    # capabilities_list: list of desiredCapabilities dicts for BrowserStack sessions
    results_all = []
    with ThreadPoolExecutor(max_workers=len(capabilities_list)) as ex:
        futures = [ex.submit(browserstack_worker, cap) for cap in capabilities_list]
        for fut in as_completed(futures):
            try:
                results_all.extend(fut.result())
            except Exception as e:
                print('A worker failed:', e)
    return results_all


if __name__ == '__main__':
    # Quick local run
    print('Run locally:')
    print('1) python this_script_name.py --local')
    print('Run on BrowserStack:')
    print('2) python this_script_name.py --bs')

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', action='store_true', help='Run the script locally using Chrome')
    parser.add_argument('--bs', action='store_true', help='Run the script on BrowserStack in parallel')
    args = parser.parse_args()

    if args.local:
        run_full_flow_local()
        
    elif args.bs:
        # Example capabilities list (5 sessions) - mix of desktop and mobile
        # THIS LIST IS NOW IN THE CORRECT W3C format
        caps = [
            {
                "browserName": "Chrome",
                "browserVersion": "latest",
                "bstack:options": {
                    "os": "Windows",
                    "osVersion": "11",
                    "sessionName": "ELPAIS-1 (Win11 Chrome)",
                    "debug": True
                }
            },
            {
                "browserName": "Firefox",
                "browserVersion": "latest",
                "bstack:options": {
                    "os": "Windows",
                    "osVersion": "10",
                    "sessionName": "ELPAIS-2 (Win10 Firefox)"
                }
            },
            {
                "browserName": "Safari", # Specify browser for mobile
                "bstack:options": {
                    "deviceName": "iPhone 14",
                    "realMobile": "true",
                    "osVersion": "16",
                    "sessionName": "ELPAIS-3 (iPhone 14)"
                }
            },
            {
                "browserName": "Chrome", # Specify browser for mobile
                "bstack:options": {
                    "deviceName": "Samsung Galaxy S22",
                    "realMobile": "true",
                    "osVersion": "12.0",
                    "sessionName": "ELPAIS-4 (Galaxy S22)"
                }
            },
            {
                "browserName": "Edge",
                "browserVersion": "latest",
                "bstack:options": {
                    "os": "OS X",
                    "osVersion": "Ventura",
                    "sessionName": "ELPAIS-5 (Mac Edge)"
                }
            }
        ]
        
        print("Running 5 parallel sessions on BrowserStack...")
        results = run_on_browserstack_parallel(caps)
        
        # --- ADDED: Translation and Analysis for BS results ---
        if not results:
            print("No results collected, skipping translation and analysis.")
            exit()

        # Dedupe results (parallel runs might get same articles)
        seen_urls = set()
        unique_results = []
        for r in results:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                unique_results.append(r)
        
        print(f"Found {len(unique_results)} unique articles.")

        titles = [r['title_es'] for r in unique_results]
        translated = []
        if HAS_GOOGLE:
            try:
                print("Translating titles...")
                translated = translate_texts_google(titles, target='en')
            except Exception as e:
                print('Google Translate failed:', e)
                translated = ['(translation failed)'] * len(titles)
        else:
            print("Google Translate library not found. Skipping translation.")
            translated = ['(google lib missing)'] * len(titles)

        for i, r in enumerate(unique_results):
            r['title_en'] = translated[i]

        # Print outputs
        for r in unique_results:
            print('\n--- ARTICLE ---')
            print('URL:', r['url'])
            print('Title (ES):', r['title_es'])
            print('Title (EN):', r['title_en'])
            print('\nBody (ES):\n', (r['body_es'][:1000] + '...') if r['body_es'] and len(r['body_es'])>1000 else r['body_es'])
            print('Cover image saved at:', r['cover_image'])
        
        # Run analysis
        repeated = analyze_translated_headers([r['title_en'] for r in unique_results])
        print('\nWords repeated more than twice in all translated headers (from unique articles):')
        if repeated:
            for w, c in repeated.items():
                print(f"'{w}': {c}")
        else:
            print("(None)")
            
    else:
        print("No run option selected. Use --local or --bs.")