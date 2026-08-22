import os
import argparse
import json
import logging
from datetime import datetime, timedelta
import concurrent.futures
from typing import Any, List, Tuple, Callable, Dict
from models import AppConfig, Database, Entry, Topic
from api_arxiv import fetch_arxiv
from api_github import fetch_github, fetch_batch_repo_stats, get_github_token
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


def enrich_github_stats(entries: List[Entry], fetch_batch_fn: Callable[[List[str]], Dict[str, dict]]) -> Tuple[List[Entry], int]:
    """Enriches entries containing a GitHub repository URL with stars, forks, and last commit timestamp in a single batch query."""
    github_entries = [e for e in entries if e.get('code_url') and 'github.com/' in e['code_url']]
    if not github_entries:
        return entries, 0

    owner_repos = []
    for e in github_entries:
        parts = e['code_url'].split('github.com/')[1].strip('/').split('/')
        if len(parts) >= 2:
            owner_repos.append(f"{parts[0]}/{parts[1]}")

    if not owner_repos:
        return entries, 0

    if not get_github_token():
        logger.warning("GITHUB_TOKEN and local 'gh' auth are not available. Skipping batch GitHub stats update to avoid rate limits.")
        return entries, 0

    logger.info(f"Enriching GitHub statistics in a single batch query for {len(owner_repos)} repositories...")
    stats_map = fetch_batch_fn(owner_repos)

    if not stats_map:
        return entries, 0

    entry_map = {e['id']: e for e in entries}
    for e in github_entries:
        parts = e['code_url'].split('github.com/')[1].strip('/').split('/')
        if len(parts) >= 2:
            owner_repo = f"{parts[0]}/{parts[1]}"
            if owner_repo in stats_map:
                entry_map[e['id']] = {**e, **stats_map[owner_repo]}

    return list(entry_map.values()), len(stats_map)

def run_pipeline(
    fetch_papers: Callable[[str, datetime], List[Entry]],
    fetch_repos: Callable[[str, datetime], List[Entry]],
    analyze_item: Callable[[Entry, AppConfig, Topic, dict[str, Any], str], Tuple[dict[str, Any], dict[str, Any]]],
    render_md: Callable[[AppConfig, Database], str],
    config: AppConfig,
    db: Database,
    start_date: datetime,
    force: bool = False,
    reanalyze_existing: bool = False
) -> Tuple[Database, str, Dict[str, Any], List[Entry]]:
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
        
        allowed_tags = topic.get('tags', topic.get('categories', ['Uncategorized']))
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": allowed_tags
                    },
                    "description": "1 to 3 relevant tags from the allowed tags list."
                },
                "summary": {
                    "type": "string",
                    "description": "A very brief 1-2 sentence summary."
                }
            },
            "required": ["tags", "summary"],
            "additionalProperties": False
        }
        
        result, usage = analyze_item(
            item, 
            config, 
            topic, 
            schema, 
            "CategorizationResult"
        )
        
        res_tags = result.get('tags', [])
        if isinstance(res_tags, str):
            res_tags = [res_tags]
        valid_tags = list(dict.fromkeys(t for t in res_tags if t in allowed_tags))
        if not valid_tags:
            valid_tags = ['Uncategorized']
            
        summary = str(result.get('summary', 'Error generating summary.')).strip().rstrip('}').strip()
        
        item_dict = dict(item)
        item_dict.pop('category', None)
        processed_entry = {**item_dict, 'tags': valid_tags, 'summary': summary, 'description': None} # type: ignore
        return processed_entry, usage

    llm_success = 0
    llm_error = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cached_tokens = 0
    total_tokens = 0
    processed_items_with_usage = []

    if items_to_process:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(process_item, items_to_process))
            
        processed_entries = []
        for pe, usage in results:
            processed_entries.append(pe)
            pe_copy = dict(pe)
            pe_copy['_usage'] = usage
            processed_items_with_usage.append(pe_copy)

            if pe.get('summary') and not pe['summary'].startswith('Error'):
                llm_success += 1
            else:
                llm_error += 1

            total_prompt_tokens += usage.get('prompt_tokens', 0)
            total_completion_tokens += usage.get('completion_tokens', 0)
            total_cached_tokens += usage.get('cached_tokens', 0)
            total_tokens += usage.get('total_tokens', 0)

        # Clean out temporary fields from DB entries
        processed_entries = [{k: v for k, v in e.items() if v is not None and k != '_usage'} for e in processed_entries]
        
        # Merge updated/new entries into database by id
        entry_map = {e['id']: e for e in entries}
        for pe in processed_entries:
            entry_map[pe['id']] = pe
        db['entries'] = list(entry_map.values())

    # Clean up legacy 'category' field from all database entries and ensure 'tags' is set
    for e in db.get('entries', []):
        if 'category' in e:
            if 'tags' not in e or not e['tags']:
                e['tags'] = [e['category']]
            del e['category']

    # Enrich all entries with GitHub statistics (stars, forks, last commit) in a single batch query
    db['entries'], enriched_count = enrich_github_stats(db['entries'], fetch_batch_repo_stats)
        
    readme_content = render_md(config, db)

    stats_summary = {
        "arxiv_fetched": len(papers),
        "github_fetched": len(repos),
        "total_fetched": len(papers) + len(repos),
        "items_analyzed": len(items_to_process),
        "llm_success": llm_success,
        "llm_error": llm_error,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "cached_tokens": total_cached_tokens,
        "total_tokens": total_tokens,
        "repos_enriched": enriched_count,
        "total_db_entries": len(db['entries'])
    }

    return db, readme_content, stats_summary, processed_items_with_usage

def generate_markdown_report(
    config: AppConfig,
    db: Database,
    stats_summary: Dict[str, Any],
    fetch_days: int,
    elapsed: float,
    processed_items: List[Entry]
) -> str:
    topic = config['topics'][0]
    llm_cfg = config.get('llm', {})
    primary_model = llm_cfg.get('model', 'N/A')
    fallback_models = ", ".join(llm_cfg.get('fallback_models', [])) or "None"
    
    entries = db.get('entries', [])
    tag_counts = {}
    for tag in topic.get('tags', []):
        tag_counts[tag] = sum(1 for e in entries if tag in e.get('tags', []))
        
    tag_breakdown_str = " | ".join([f"**{tag}**: {count}" for tag, count in tag_counts.items()])

    report = "## 📊 Automated Pipeline Execution Report\n\n"
    report += "### 📈 Overview Metrics\n\n"
    report += "| Metric | Details |\n"
    report += "| :--- | :--- |\n"
    report += f"| 📅 **Fetch Interval** | Past {fetch_days} days |\n"
    report += f"| 📄 **arXiv Papers Fetched** | {stats_summary['arxiv_fetched']} |\n"
    report += f"| 💻 **GitHub Repos Fetched** | {stats_summary['github_fetched']} |\n"
    report += f"| 🔍 **Total Items Analyzed** | {stats_summary['items_analyzed']} |\n"
    report += f"| ✅ **LLM Analysis Success** | {stats_summary['llm_success']} |\n"
    report += f"| ❌ **LLM Analysis Failed** | {stats_summary['llm_error']} |\n"
    report += f"| ⭐ **GitHub Stats Enriched** | {stats_summary['repos_enriched']} repos |\n"
    report += f"| 📚 **Total Database Entries** | {stats_summary['total_db_entries']} |\n"
    report += f"| ⏱️ **Total Execution Time** | {elapsed:.2f} seconds |\n\n"

    report += "### 🔢 Token Usage Metrics\n\n"
    report += "| Token Metric | Count |\n"
    report += "| :--- | :--- |\n"
    report += f"| 📥 **Input Tokens (Prompt)** | {stats_summary.get('prompt_tokens', 0):,} |\n"
    report += f"| 📤 **Output Tokens (Completion)** | {stats_summary.get('completion_tokens', 0):,} |\n"
    report += f"| ⚡ **Cached Tokens** | {stats_summary.get('cached_tokens', 0):,} |\n"
    report += f"| 🧮 **Total Tokens Used** | {stats_summary.get('total_tokens', 0):,} |\n\n"

    report += "### 🏷️ Database Tag Breakdown\n\n"
    report += f"{tag_breakdown_str}\n\n"

    report += "### 🤖 LLM Models Configured\n\n"
    report += f"- **Primary Model**: `{primary_model}`\n"
    report += f"- **Fallback Models**: `{fallback_models}`\n\n"

    report += "### 📝 Items Analyzed / Processed in this Run\n\n"
    if processed_items:
        for idx, item in enumerate(processed_items, 1):
            title = item.get('title', 'Untitled')
            year = item.get('year', '')
            year_str = f" ({year})" if year else ""
            tags_str = ", ".join(item.get('tags', ['Uncategorized']))
            usage = item.get('_usage', {})
            
            report += f"{idx}. **{title}**{year_str}\n"
            if item.get('paper_url'):
                report += f"   - **Paper**: {item['paper_url']}\n"
            if item.get('code_url'):
                report += f"   - **Code**: {item['code_url']}\n"
            report += f"   - **Tags**: {tags_str}\n"
            if item.get('stars') is not None:
                report += f"   - **Stats**: Stars: {item.get('stars', 0)} | Forks: {item.get('forks', 0)} | Last Commit: {item.get('last_commit', 'N/A')}\n"
            if usage:
                report += f"   - **Tokens**: Input: {usage.get('prompt_tokens', 0):,} | Output: {usage.get('completion_tokens', 0):,} | Cached: {usage.get('cached_tokens', 0):,} | Total: {usage.get('total_tokens', 0):,} ({usage.get('model', 'N/A')})\n"
            if item.get('summary'):
                report += f"   - **Summary**: {item['summary']}\n"
            report += "\n"
    else:
        report += "No new items were required to be analyzed in this run. Database is up to date.\n\n"

    report += "---\n*Report generated automatically by the update pipeline.*"
    return report
def main() -> None:
    logger.info("Starting monthly update process.")
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
        fetch_days = config.get('settings', {}).get('fetch_days', 30)

    logger.info(f"Fetching entries for the past {fetch_days} days.")
    start_date = start_time - timedelta(days=fetch_days)
    
    updated_db, readme_content, stats_summary, processed_items = run_pipeline(
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
    
    elapsed = (datetime.now() - start_time).total_seconds()
    report_content = generate_markdown_report(config, updated_db, stats_summary, fetch_days, elapsed, processed_items)

    # Execute side-effects safely at boundary
    save_db = lambda d: write_json('data/database.json', d)
    save_readme = lambda c: write_file('README.md', c)
    save_report = lambda r: write_file('report.md', r)
    
    save_db(updated_db)
    save_readme(readme_content)
    save_report(report_content)

    summary_msg = (
        "\n" + "=" * 50 + "\n"
        "              EXECUTION SUMMARY\n"
        "==================================================\n"
        f"- Fetch Interval         : Past {fetch_days} days\n"
        f"- arXiv Papers Fetched   : {stats_summary['arxiv_fetched']}\n"
        f"- GitHub Repos Fetched   : {stats_summary['github_fetched']}\n"
        f"- Total Items Fetched    : {stats_summary['total_fetched']}\n"
        f"- Items Analyzed (LLM)   : {stats_summary['items_analyzed']}\n"
        f"- LLM Analysis Success   : {stats_summary['llm_success']}\n"
        f"- LLM Analysis Failed    : {stats_summary['llm_error']}\n"
        f"- Input Tokens (Prompt)  : {stats_summary.get('prompt_tokens', 0):,}\n"
        f"- Output Tokens (Completion): {stats_summary.get('completion_tokens', 0):,}\n"
        f"- Cached Tokens          : {stats_summary.get('cached_tokens', 0):,}\n"
        f"- Total Tokens Used      : {stats_summary.get('total_tokens', 0):,}\n"
        f"- Repos Enriched (Stats) : {stats_summary['repos_enriched']}\n"
        f"- Total Database Entries : {stats_summary['total_db_entries']}\n"
        f"- Total Execution Time   : {elapsed:.2f} seconds\n"
        "=================================================="
    )
    logger.info(summary_msg)

if __name__ == "__main__":
    main()
