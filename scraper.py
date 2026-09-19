import os
import sys
import re
import requests
import datetime
from bs4 import BeautifulSoup
import serpapi
import pandas as pd

# ==========================================
# 1. READ CONFIG FROM GITHUB SECRETS / ENV
# ==========================================
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

if not SERPAPI_KEY or not N8N_WEBHOOK_URL:
    print("Error: SERPAPI_KEY or N8N_WEBHOOK_URL environment variables are missing.")
    sys.exit(1)

client = serpapi.Client(api_key=SERPAPI_KEY)

# Expanded list of target UAE keywords
keywords = [
    "doctor", "gym", "dentist", "pharmacy", "salon", 
    "spa", "dermatologist", "clinic", "car rental", 
    "real estate agency", "restaurant", "cleaning service", 
    "nursery", "veterinarian", "accounting firm", 
    "law firm", "interior design", "flower shop"
]

results_per_keyword = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==========================================
# 2. DAILY KEYWORD ROTATION LOGIC
# ==========================================
# Rotation start date: September 20, 2026
START_DATE = datetime.date(2026, 9, 20)
today = datetime.date.today()

days_elapsed = (today - START_DATE).days

if days_elapsed < 0:
    print(f"Script configured to start on {START_DATE}. Exiting for today.")
    sys.exit(0)

# Rotates through keywords sequentially (1 per day)
current_keyword_index = days_elapsed % len(keywords)
target_keyword = keywords[current_keyword_index]

print(f"=== Running Scraping Job for Date: {today} ===")
print(f"Today's Selected Keyword [{current_keyword_index + 1}/{len(keywords)}]: '{target_keyword}'")

# ==========================================
# 3. HELPER FUNCTION (BS4 WEB SCRAPER)
# ==========================================
def scrape_contact_details(url):
    if not url or url == "N/A" or not url.startswith("http"):
        return "N/A", "N/A"

    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code != 200:
            return "N/A", "N/A"

        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text()

        # Extract Emails
        raw_emails = set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text))
        emails = [e for e in raw_emails if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'))]

        # Extract UAE Phone Numbers
        phone_matches = set(re.findall(r'(?:\+971|00971|0)?(?:5[01245689]|4|2|3|6|7|9|800)\d{7}', text))

        email_str = ", ".join(emails[:3]) if emails else "N/A"
        phone_str = ", ".join(phone_matches[:3]) if phone_matches else "N/A"

        return email_str, phone_str

    except Exception:
        return "N/A", "N/A"

# ==========================================
# 4. SERPAPI MAPS SCRAPING
# ==========================================
all_results = []

for start in range(0, results_per_keyword, 20):
    print(f"Scraping SerpApi Google Maps for '{target_keyword}' (start index: {start})...")
    
    params = {
        "engine": "google_maps",
        "q": f"{target_keyword} in UAE",
        "gl": "ae",
        "hl": "en",
        "start": start
    }

    try:
        results = client.search(params)
        local_results = results.get("local_results", [])
        
        if not local_results:
            print(f"No more results found for '{target_keyword}'.")
            break

        for place in local_results:
            all_results.append({
                "Tag": target_keyword,
                "Title": place.get("title"),
                "Maps Phone": place.get("phone", "N/A"),
                "Website/Link": place.get("website", place.get("link", "N/A")),
                "Address": place.get("address", "N/A"),
                "Rating": place.get("rating", "N/A"),
                "Reviews": place.get("reviews", 0)
            })
            
    except Exception as e:
        print(f"SerpApi Error: {e}")
        break

# ==========================================
# 5. BS4 ENRICHMENT, FILTERING & WEBHOOK
# ==========================================
if all_results:
    df = pd.DataFrame(all_results)
    
    # Deduplicate before scraping websites
    df.drop_duplicates(subset=["Title", "Maps Phone"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    print(f"\n--- Scraping Websites for {len(df)} Unique Places ---")
    
    website_emails = []
    website_phones = []

    for idx, row in df.iterrows():
        url = row["Website/Link"]
        print(f"[{idx + 1}/{len(df)}] Scraping: {url}")
        
        email, phone = scrape_contact_details(url)
        website_emails.append(email)
        website_phones.append(phone)

    df["Website Email"] = website_emails
    df["Website Phone"] = website_phones

    # Filter: Keep entries where Email OR Phone is found on the website
    filtered_df = df[(df["Website Email"] != "N/A") | (df["Website Phone"] != "N/A")]

    print(f"\nTotal Leads Extracted: {len(df)}")
    print(f"Valid Enriched Leads (Non-N/A): {len(filtered_df)}")

    # Send payload to n8n Webhook
    if not filtered_df.empty:
        payload = {
            "date": str(today),
            "keyword": target_keyword,
            "count": len(filtered_df),
            "leads": filtered_df.to_dict(orient="records")
        }

        print(f"Sending payload to n8n Webhook...")
        try:
            webhook_res = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=10)
            if webhook_res.status_code in [200, 201]:
                print("Successfully sent data to n8n!")
            else:
                print(f"Failed to send to n8n. Status code: {webhook_res.status_code}")
        except Exception as e:
            print(f"Error sending webhook to n8n: {e}")
    else:
        print("No enriched leads found today to send to n8n.")

else:
    print("No SerpApi results collected.")
