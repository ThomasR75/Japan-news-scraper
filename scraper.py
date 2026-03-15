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

def scrape_rss_feed(source_name, rss_url):
    """
    Scrapes articles from an RSS feed.
    :param source_name: Name of the news source.
    :param rss_url: URL of the RSS feed.
    :return: List of dictionaries, each representing an article.
    """
    print(f"Scraping RSS feed for {source_name} from {rss_url}")
    feed = feedparser.parse(rss_url)
    articles = []
    for entry in feed.entries:
        try:
            article_url = entry.link
            response = requests.get(article_url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'lxml')
            
            # This is a generic attempt to get full text. Will need refinement per source.
            article_body_tags = soup.find_all(['p', 'div'], class_=['article-body', 'content', 'main-text', 'article-text', 'c-article-body'])
            article_text = "\n".join([p.get_text(separator=' ', strip=True) for p in article_body_tags])
            if not article_text:
                 article_text = soup.get_text(separator=' ', strip=True)

            article_data = {
                'source': source_name,
                'url': article_url,
                'raw_html': response.text,
                'extracted_text': article_text,
                'title': entry.title if hasattr(entry, 'title') else 'No Title',
                'published_date': entry.published if hasattr(entry, 'published') else datetime.now().isoformat(),
                'language': 'ja',
                'status': 'raw'
            }
            save_raw_article_data(source_name, article_data)
            articles.append(article_data)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching article {entry.link}: {e}")
        except Exception as e:
            print(f"Error processing RSS entry {entry.link}: {e}")
    return articles

def scrape_html_main_page(source_name, base_url, article_selector, title_selector, date_selector, text_selector):
    """
    Generic function to scrape articles from a main news listing page.
    Requires specific CSS selectors for the article links, titles, dates, and full text.
    This will need to be highly customized for each HTML-scraped source.
    """
    print(f"Scraping main HTML page for {source_name} from {base_url}")
    articles = []
    try:
        response = requests.get(base_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'lxml')

        # Find all article links on the main page
        article_links = [a['href'] for a in soup.select(article_selector) if 'href' in a.attrs]
        
        for link in set(article_links): # Use set to avoid duplicates
            if not link.startswith('http'):
                link = requests.compat.urljoin(base_url, link) # Resolve relative URLs

            try:
                article_response = requests.get(link, timeout=10)
                article_response.raise_for_status()
                article_soup = BeautifulSoup(article_response.content, 'lxml')

                title = article_soup.select_one(title_selector).get_text(strip=True) if article_soup.select_one(title_selector) else 'No Title'
                
                # Dynamic date parsing might be complex, start with a generic approach
                published_date = article_soup.select_one(date_selector).get_text(strip=True) if article_soup.select_one(date_selector) else datetime.now().isoformat()

                article_text_elements = article_soup.select(text_selector)
                article_text = "\n".join([p.get_text(separator=' ', strip=True) for p in article_text_elements])
                if not article_text:
                    article_text = article_soup.get_text(separator=' ', strip=True)


                article_data = {
                    'source': source_name,
                    'url': link,
                    'raw_html': article_response.text,
                    'extracted_text': article_text,
                    'title': title,
                    'published_date': published_date,
                    'language': 'ja',
                    'status': 'raw'
                }
                save_raw_article_data(source_name, article_data)
                articles.append(article_data)
            except requests.exceptions.RequestException as e:
                print(f"Error fetching article {link}: {e}")
            except Exception as e:
                print(f"Error parsing article {link}: {e}")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching main page {base_url}: {e}")
    return articles

def main_scraper():
    """
    Main function to orchestrate scraping for all defined sources.
    """
    print("Starting main scraper execution...")

    # --- RSS Feeds ---
    # Nikkei Business Electronic Edition (multiple feeds)
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

    # --- HTML Scraping (will need selector customization) ---
    # Yomiuri Shimbun - Placeholder selectors
    # Actual selectors will need to be determined by inspecting the Yomiuri website structure
    # For now, using generic examples.
    print("\n--- Starting HTML Scraping ---")
    scrape_html_main_page(
        source_name="Yomiuri Shimbun",
        base_url="https://www.yomiuri.co.jp/",
        article_selector=".p-category-article__item a", # Example selector
        title_selector=".p-article-header__title",      # Example selector
        date_selector=".p-article-header__date",        # Example selector
        text_selector=".p-article-body__text p"          # Example selector
    )

    # Sankei Shimbun - Placeholder selectors
    scrape_html_main_page(
        source_name="Sankei Shimbun",
        base_url="https://www.sankei.com/",
        article_selector=".gr-article-list__item a",    # Example selector
        title_selector=".article-header .title",        # Example selector
        date_selector=".article-header .date",          # Example selector
        text_selector=".article-text p"                  # Example selector
    )

    # Fuji TV News - Placeholder selectors
    scrape_html_main_page(
        source_name="Fuji TV News",
        base_url="https://www.fujitv.co.jp/news/", # Or https://www.fujitv.com/news/
        article_selector=".news-item a",             # Example selector
        title_selector=".news-title",               # Example selector
        date_selector=".news-date",                 # Example selector
        text_selector=".news-content p"              # Example selector
    )

    # TBS News DIG - Placeholder selectors
    scrape_html_main_page(
        source_name="TBS News DIG",
        base_url="https://newsdig.tbs.co.jp/",
        article_selector=".article-list__item a",    # Example selector
        title_selector=".article-detail__title",     # Example selector
        date_selector=".article-detail__date",       # Example selector
        text_selector=".article-detail__body p"      # Example selector
    )

    print("Main scraper execution complete.")

if __name__ == "__main__":
    # Ensure the base directory exists
    os.makedirs(ARCHIVE_BASE_DIR, exist_ok=True)
    main_scraper()
