import requests
from bs4 import BeautifulSoup
import feedparser
import json
import os
from datetime import datetime

# Base directory for storing news archives
ARCHIVE_BASE_DIR = 'data/news_archive/raw'

def save_raw_article_data(source, article_data):
    """
    Saves the raw article data as a JSON file.
    :param source: Name of the news source (e.g., 'ShukanBunshun')
    :param article_data: Dictionary containing article details.
    """
    source_dir = os.path.join(ARCHIVE_BASE_DIR, source)
    os.makedirs(source_dir, exist_ok=True)

    # Generate a unique filename based on publish date and URL hash
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    article_id = hash(article_data['url']) % (10**8)
    filename = f"{timestamp_str}_{article_id}.json"
    filepath = os.path.join(source_dir, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(article_data, f, ensure_ascii=False, indent=4)
    print(f"Saved raw article from {source} to {filepath}")

def extract_article_details(soup, url, source_name, title_selector, date_selector, text_selector):
    """
    Extracts article details (title, date, text) from a BeautifulSoup object.
    """
    title = soup.select_one(title_selector).get_text(strip=True) if soup.select_one(title_selector) else 'No Title'
    
    # Generic date parsing
    date_element = soup.select_one(date_selector)
    published_date = None
    if date_element:
        if date_element.has_attr('datetime'):
            published_date = date_element['datetime']
        else:
            published_date = date_element.get_text(strip=True)
    if not published_date:
        published_date = datetime.now().isoformat() # Fallback

    article_text_elements = soup.select(text_selector)
    article_text = "\n".join([p.get_text(separator=' ', strip=True) for p in article_text_elements])
    if not article_text:
        article_text = soup.get_text(separator=' ', strip=True) # Fallback if specific classes not found

    return {
        'source': source_name,
        'url': url,
        'extracted_text': article_text,
        'title': title,
        'published_date': published_date,
        'language': 'ja', # Assuming Japanese
        'status': 'raw'
    }

def scrape_rss_feed(source_name, rss_url):
    """
    Scrapes articles from an RSS feed.
    """
    print(f"Scraping RSS feed for {source_name} from {rss_url}")
    feed = feedparser.parse(rss_url)
    articles_data = [] # Changed name to avoid conflict with RSS feed 'entries'

    for entry in feed.entries:
        try:
            article_url = entry.link
            response = requests.get(article_url, timeout=15) # Increased timeout
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Since RSS often includes full content or good summary, we can try to extract directly from entry
            # or rely on the extract_article_details if a generic way is needed.
            # For RSS, the `entry` often has title and published date directly.
            title = entry.title if hasattr(entry, 'title') else 'No Title'
            published_date = entry.published if hasattr(entry, 'published') else datetime.now().isoformat()
            
            # Attempt to get text from entry summary/content or from fetched page
            extracted_text = entry.summary if hasattr(entry, 'summary') else ''
            if not extracted_text and hasattr(entry, 'content'):
                extracted_text = entry.content[0].value if entry.content else ''
            
            # If still empty, try to extract from the soup (generic approach) - this should be overridden by actual selectors if needed
            if not extracted_text:
                article_body_tags = soup.find_all(['p', 'div'], class_=['article-body', 'content', 'main-text', 'article-text', 'c-article-body'])
                extracted_text = "\n".join([p.get_text(separator=' ', strip=True) for p in article_body_tags])
                if not extracted_text:
                     extracted_text = soup.get_text(separator=' ', strip=True)

            article_data = {
                'source': source_name,
                'url': article_url,
                'raw_html': response.text,
                'extracted_text': extracted_text,
                'title': title,
                'published_date': published_date,
                'language': 'ja',
                'status': 'raw'
            }
            save_raw_article_data(source_name, article_data)
            articles_data.append(article_data)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching RSS article {entry.link}: {e}")
        except Exception as e:
            print(f"Error processing RSS entry {entry.link}: {e}")
    return articles_data


def scrape_html_source(source_name, base_url, article_link_selector, title_selector, date_selector, text_selector, max_listing_pages=1):
    """
    Scrapes articles from a main news listing page and then individual article pages.
    """
    print(f"Scraping HTML for {source_name} from {base_url}")
    articles_collected = []
    visited_links = set()

    for page_num in range(max_listing_pages):
        listing_url = f"{base_url}page/{page_num + 1}/" if max_listing_pages > 1 and page_num > 0 else base_url
        print(f"  Fetching listing page: {listing_url}")
        try:
            response = requests.get(listing_url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'lxml')

            # Find all potential article links on the listing page
            found_links = []
            for a_tag in soup.select(article_link_selector):
                if 'href' in a_tag.attrs:
                    link = requests.compat.urljoin(base_url, a_tag['href'])
                    found_links.append(link)
            
            if not found_links and page_num == 0:
                print(f"    No article links found on the first listing page for {source_name} with selector {article_link_selector}. Adjusting strategy...")
                # Could add a fallback here to try different selectors or directly scrape the main page if it's a single article layout

            for link in found_links:
                if link in visited_links:
                    continue
                visited_links.add(link);

                try:
                    article_response = requests.get(link, timeout=15)
                    article_response.raise_for_status()
                    article_soup = BeautifulSoup(article_response.content, 'lxml')

                    article_details = extract_article_details(article_soup, link, source_name, title_selector, date_selector, text_selector)
                    article_details['raw_html'] = article_response.text # Add raw HTML here
                    save_raw_article_data(source_name, article_details)
                    articles_collected.append(article_details)
                except requests.exceptions.RequestException as e:
                    print(f"    Error fetching article {link}: {e}")
                except Exception as e:
                    print(f"    Error parsing article {link}: {e}")
            
            if not found_links and page_num > 0: # Stop if no new links on subsequent pages
                break

        except requests.exceptions.RequestException as e:
            print(f"  Error fetching listing page {listing_url}: {e}")
            break
        except Exception as e:
            print(f"  Error processing listing page {listing_url}: {e}")
            break
    return articles_collected


def main_scraper():
    """
    Main function to orchestrate scraping for all defined sources.
    """
    print("Starting main scraper execution...")

    # --- RSS Feeds ---
    # Nikkei Business Electronic Edition
    nikkei_business_feeds = {
        "Nikkei Business Latest": "https://business.nikkei.com/rss/sns/nb.rdf",
        "Nikkei Business X": "https://business.nikkei.com/rss/sns/nb-x.rdf",
        "Nikkei Business Plus": "https://business.nikkei.com/rss/sns/nb-plus.rdf",
        "Nikkei Business LIVE": "https://business.nikkei.com/rss/sns/nb-live.rdf",
    }
    for name, url in nikkei_business_feeds.items():
        scrape_rss_feed(name, url)

    # Asahi Shimbun
    scrape_rss_feed("Asahi Shimbun", "http://www.asahi.com/rss/asahi/newsheadlines.rdf")

    # Mainichi Shimbun (using News Flash Overall)
    scrape_rss_feed("Mainichi Shimbun", "https://mainichi.jp/rss/etc/mainichi-flash.rss")

    # NHK News
    scrape_rss_feed("NHK News", "https://news.web.nhk/n-data/conf/na/rss/cat0.xml")

    # --- HTML Scraping ---
    print("\n--- Starting HTML Scraping ---")

    # Yomiuri Shimbun
    scrape_html_source(
        source_name="Yomiuri Shimbun",
        base_url="https://www.yomiuri.co.jp/", # Will need exact section URLs as homepage is dynamic and complex
        article_link_selector=".p-list-item__link", # Use a common article link selector
        title_selector="h1.p-article__title",
        date_selector=".p-article__date",
        text_selector=".p-article__text p"
    )

    # Sankei Shimbun
    scrape_html_source(
        source_name="Sankei Shimbun",
        base_url="https://www.sankei.com/",
        article_link_selector="a[href^='/article/']", 
        title_selector="h1.article-headline",
        date_selector="time", 
        text_selector=".article-body p.article-text"
    )

    # Fuji TV News (FNN)
    scrape_html_source(
        source_name="Fuji TV News",
        base_url="https://www.fnn.jp/", 
        article_link_selector=".m-article-item__link",
        title_selector="h1.article-header-info__ttl",
        date_selector="time.article-header-info__time", 
        text_selector=".article-body p"
    )

    # TBS News DIG
    scrape_html_source(
        source_name="TBS News DIG",
        base_url="https://newsdig.tbs.co.jp/",
        article_link_selector="main article a[href^='/articles/-/']", 
        title_selector="h1.article-header-title",
        date_selector="time.article-header-time", 
        text_selector=".article-body p"
    )

    print("Main scraper execution complete.")

if __name__ == "__main__":
    # Ensure the base directory exists
    os.makedirs(ARCHIVE_BASE_DIR, exist_ok=True)
    main_scraper()
