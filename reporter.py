import os
import json
import glob
from datetime import datetime

# Base directory for raw articles (now containing summaries)
RAW_ARCHIVE_BASE_DIR = 'data/news_archive/raw'

def generate_daily_digest():
    """
    Generates a daily digest of summarized articles.
    """
    print("Starting daily digest generation...")

    digest = {}
    # Find all JSON files that have been summarized
    json_files = glob.glob(os.path.join(RAW_ARCHIVE_BASE_DIR, '*', '*.json'))

    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                article_data = json.load(f)
            
            if article_data.get('summarization_status') == 'summarized_by_llm':
                source = article_data.get('source', 'Unknown Source')
                if source not in digest:
                    digest[source] = []
                digest[source].append(article_data)

        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from {file_path}: {e}")
        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
    
    # Generate Markdown report
    report_date = datetime.now().strftime('%Y-%m-%d')
    report_filename = f"daily_digest_{report_date}.md"
    report_filepath = os.path.join('data/reports', report_filename)
    os.makedirs(os.path.dirname(report_filepath), exist_ok=True)

    with open(report_filepath, 'w', encoding='utf-8') as f:
        f.write(f"# Daily News Digest - {report_date}\n\n")
        f.write("Generated from Japanese News Scraper Project\n\n")

        for source, articles in digest.items():
            f.write(f"## {source}\n\n")
            for article in articles:
                original_title = article.get('title', 'N/A')
                translated_text = article.get('translated_text', '')
                summary = article.get('summary', 'N/A')
                url = article.get('url', '#')
                published_date = article.get('published_date', 'N/A')

                f.write(f"### [{original_title}]({url})\n")
                f.write(f"* **Published:** {published_date}\n")
                # f.write(f"* **Translated Excerpt:** {translated_text[:200]}...\n") # Optionally include a translated excerpt
                f.write(f"* **Summary:** {summary}\n\n")
            f.write("\n---\n\n")
    
    print(f"Daily digest generated: {report_filepath}")

if __name__ == "__main__":
    generate_daily_digest()