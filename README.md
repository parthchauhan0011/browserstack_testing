# El País Opinion Scraper & Analyzer

This Python script uses Selenium and BrowserStack to scrape, translate, and analyze articles from the "Opinión" section of El País.

## Features

* **Scrapes El País:** Navigates to `elpais.com/opinion/` in Spanish.
* **Fetches Articles:** Scrapes the title, body, and cover image for the first 5 articles.
* **Translates Headers:** Uses the Google Cloud Translate API to translate article titles from Spanish to English.
* **Analyzes Frequency:** Identifies and counts words repeated more than twice across all translated titles.
* **Dual Mode:** Can be run locally (using Chrome) or remotely on BrowserStack.
* **Parallel Execution:** Runs 5 parallel sessions on BrowserStack across different desktop and mobile browsers.

---

## Setup

1.  **Clone the Repository:**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-name>
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    # On macOS/Linux
    python3 -m venv venv
    source venv/bin/activate
    

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

---

## Configuration (Environment Variables)

This script requires API keys and credentials, which must be set as environment variables. **Do not hardcode your keys in the script.**

### 1. BrowserStack Credentials
```bash
# On macOS/Linux
export BROWSERSTACK_USERNAME="your_bs_username"
export BROWSERSTACK_ACCESS_KEY="your_bs_access_key"

# On Windows (Command Prompt)
set BROWSERSTACK_USERNAME="your_bs_username"
set BROWSERSTACK_ACCESS_KEY="your_bs_access_key"
```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    # On macOS/Linux
    python3 -m venv venv
    source venv/bin/activate
    
    # On Windows
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

Create a service account and download its JSON key file.

Save the file (e.g., gcp-key.json) in a secure location outside of your project repository.

Set the environment variable to point to this file:

Bash

# On macOS/Linux
```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/gcp-key.json"
```


1. Run Locally
This runs the full scrape-and-translate process on your local machine using Chrome.

```bash
python sele.py --local
```

2. Run on BrowserStack (Parallel)
This runs the scraping process across 5 parallel browsers on BrowserStack, then collects, translates, and analyzes the results.

```bash
python sele.py --bs
```
