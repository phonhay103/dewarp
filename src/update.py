import os
import argparse
import json
import logging
from datetime import datetime, timedelta
import concurrent.futures
from typing import Any, List, Tuple, Callable, Dict
from models import AppConfig, Database, Entry, Topic
from api_arxiv import fetch_arxiv
from api_github import fetch_github, fetch_batch_repo_stats
from api_openrouter import analyze_with_llm
from readme_renderer import render_readme

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def read_json(filepath: str, default: Any) -> Any:
    """Reads a JSON file safely."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {e}")
        return default

def write_json(filepath: str, data: Any) -> None:
    """Writes data to a JSON file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def write_file(filepath: str, content: str) -> None:
    """Writes string content to a text file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)


def enrich_github_stats(entries: List[Entry], fetch_batch_fn: Callable[[List[str]], Dict[str, dict]]) -> List[Entry]:
    """Enriches entries containing a GitHub repository URL with stars, forks, and last commit timestamp in a single batch query."""
    github_entries = [e for e in entries if e.get('code_url') and 'github.com/' in e['code_url']]
    if not github_entries:
        return entries

    owner_repos = []
    for e in github_entries:
        parts = e['code_url'].split('github.com/')[1].strip('/').split('/')
        if len(parts) >= 2:
            owner_repos.append(f"{parts[0]}/{parts[1]}")

    if not owner_repos:
        return entries

    if not os.environ.get("GITHUB_TOKEN"):
        logger.warning("GITHUB_TOKEN is not set. Skipping batch GitHub stats update to avoid rate limits.")
        return entries

    logger.info(f"Enriching GitHub statistics in a single batch query for {len(owner_repos)} repositories...")
    stats_map = fetch_batch_fn(owner_repos)

    if not stats_map:
        return entries

    entry_map = {e['id']: e for e in entries}
    for e in github_entries:
        parts = e['code_url'].split('github.com/')[1].strip('/').split('/')
        if len(parts) >= 2:
            owner_repo = f"{parts[0]}/{parts[1]}"
            if owner_repo in stats_map:
                entry_map[e['id']] = {**e, **stats_map[owner_repo]}

    return list(entry_map.values())

def run_pipeline(
    fetch_papers: Callable[[str, datetime], List[Entry]],
    fetch_repos: Callable[[str, datetime], List[Entry]],
    analyze_item: Callable[[Entry, AppConfig, Topic, dict[str, Any], str], dict[str, Any]],
    render_md: Callable[[AppConfig, Database], str],
    config: AppConfig,
    db: Database,
    start_date: datetime,
    force: bool = False,
    reanalyze_existing: bool = False
) -> Tuple[Database, str]:
    """
    Executes the main automated update pipeline functionally.
    
    Returns:
        Tuple[Database, str]: The updated database and the generated README content.
    """
    topic = config['topics'][0]
    
    logger.info("Fetching arXiv and GitHub concurrently...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_arxiv = executor.submit(fetch_papers, topic['queries']['arxiv'], start_date)
        future_github = executor.submit(fetch_repos, topic['queries']['github'], start_date)
        papers = future_arxiv.result()
        repos = future_github.result()
    
    entries = db.get('entries', [])

    if reanalyze_existing:
        logger.info(f"Re-analyze mode enabled: updating all {len(entries)} existing database entries without fetching new ones.")
        items_to_process = list(entries)
    else:
        logger.info("Fetching arXiv and GitHub concurrently...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_arxiv = executor.submit(fetch_papers, topic['queries']['arxiv'], start_date)
            future_github = executor.submit(fetch_repos, topic['queries']['github'], start_date)
            papers = future_arxiv.result()
            repos = future_github.result()

        if force:
            logger.info("Force mode enabled: re-analyzing all fetched entries.")
            valid_ids = set()
            valid_paper_urls = set()
            valid_code_urls = set()
        else:
            # Only treat entries as valid existing if they don't have error summaries
            valid_entries = [e for e in entries if e.get('summary') and not e.get('summary', '').startswith('Error')]
            valid_ids = {e['id'] for e in valid_entries}
            valid_paper_urls = {e['paper_url'] for e in valid_entries if e.get('paper_url')}
            valid_code_urls = {e['code_url'] for e in valid_entries if e.get('code_url')}

        items_to_process = [p for p in papers if p['id'] not in valid_ids and p['paper_url'] not in valid_paper_urls]
        items_to_process.extend(r for r in repos if r['id'] not in valid_ids and r['code_url'] not in valid_code_urls)

    logger.info(f"Found {len(items_to_process)} entries to analyze.")
    
    def process_item(item: Entry) -> Entry:
        logger.info(f"Analyzing: {item['title']}")
        
        schema = {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": topic['categories']
                },
                "summary": {
                    "type": "string",
                    "description": "A very brief 1-2 sentence summary."
                }
            },
            "required": ["category", "summary"],
            "additionalProperties": False
        }
        
        result = analyze_item(
            item, 
            config, 
            topic, 
            schema, 
            "CategorizationResult"
        )
        
        cat = result.get('category', 'Uncategorized')
        if cat not in topic['categories']:
            cat = 'Uncategorized'
            
        summary = result.get('summary', 'Error generating summary.')
        
        return {**item, 'category': cat, 'summary': summary, 'description': None} # type: ignore

    if items_to_process:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            processed_entries = list(executor.map(process_item, items_to_process))
            
        # Clean out temporary fields
        processed_entries = [{k: v for k, v in e.items() if v is not None} for e in processed_entries]
        
        # Merge updated/new entries into database by id
        entry_map = {e['id']: e for e in entries}
        for pe in processed_entries:
            entry_map[pe['id']] = pe
        db['entries'] = list(entry_map.values())

    # Enrich all entries with GitHub statistics (stars, forks, last commit) in a single batch query
    db['entries'] = enrich_github_stats(db['entries'], fetch_batch_repo_stats)
        
    readme_content = render_md(config, db)
    return db, readme_content

def main() -> None:
    logger.info("Starting weekly update process.")
    start_time = datetime.now()
    
    config = read_json('config.json', {})
    db = read_json('data/database.json', {"entries": []})
    
    parser = argparse.ArgumentParser(description="Fetch and analyze arXiv papers and GitHub repos.")
    parser.add_argument('--days', type=int, help="Number of days back to fetch entries")
    parser.add_argument('--force', action='store_true', help="Force re-analyzing existing entries")
    parser.add_argument('--reanalyze-existing', action='store_true', help="Re-analyze all existing database entries without fetching new ones")
    args, _ = parser.parse_known_args()

    env_force = os.environ.get('FORCE_UPDATE', '').lower() in ('true', '1', 'yes')
    force = args.force or env_force

    env_reanalyze = os.environ.get('REANALYZE_EXISTING', '').lower() in ('true', '1', 'yes')
    reanalyze_existing = args.reanalyze_existing or env_reanalyze

    # Determine fetch interval: CLI arg > Env var > config.json > default 7
    env_days = os.environ.get('FETCH_DAYS')
    if args.days is not None:
        fetch_days = args.days
    elif env_days and env_days.isdigit():
        fetch_days = int(env_days)
    else:
        fetch_days = config.get('settings', {}).get('fetch_days', 7)

    logger.info(f"Fetching entries for the past {fetch_days} days.")
    start_date = start_time - timedelta(days=fetch_days)
    
    updated_db, readme_content = run_pipeline(
        fetch_papers=fetch_arxiv,
        fetch_repos=fetch_github,
        analyze_item=analyze_with_llm,
        render_md=render_readme,
        config=config,
        db=db,
        start_date=start_date,
        force=force,
        reanalyze_existing=reanalyze_existing
    )
    
    # Execute side-effects safely at boundary
    save_db = lambda d: write_json('data/database.json', d)
    save_readme = lambda c: write_file('README.md', c)
    
    save_db(updated_db)
    save_readme(readme_content)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Done! Pipeline completed in {elapsed:.2f} seconds.")

if __name__ == "__main__":
    main()
