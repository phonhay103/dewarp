import os
import json
import urllib.parse
import urllib.request
import logging
from datetime import datetime
from typing import List
from models import Entry
logger = logging.getLogger(__name__)

def fetch_github(query: str, start_date: datetime) -> List[Entry]:
    """
    Fetches repositories from the GitHub API based on a query and a start date.
    
    Args:
        query (str): The search query for GitHub.
        start_date (datetime): The cutoff date for repositories to include.
        
    Returns:
        List[Entry]: A list of repository metadata dictionaries.
    """
    date_str = start_date.strftime('%Y-%m-%d')
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}+created:>={date_str}&sort=stars&order=desc"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Automated-Repo-Fetcher")
    
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        req.add_header("Authorization", f"token {github_token}")
        
    results = []
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            for item in data.get('items', []):
                pushed_at = item.get('pushed_at', '')
                if pushed_at and 'T' in pushed_at:
                    last_commit = pushed_at.replace("T", " ").replace("Z", " UTC")
                else:
                    last_commit = pushed_at

                results.append({
                    "id": str(item['id']),
                    "title": item['full_name'],
                    "paper_url": "",
                    "code_url": item['html_url'],
                    "date": item['created_at'][:10],
                    "year": item['created_at'][:4],
                    "authors": item.get('owner', {}).get('login', ''),
                    "description": item.get('description', '') or "",
                    "stars": item.get('stargazers_count', 0),
                    "forks": item.get('forks_count', 0),
                    "last_commit": last_commit
                })
    except Exception as e:
        logger.error(f"Error fetching GitHub data: {e}")
        
    return results


def fetch_repo_stats(owner_repo: str) -> dict:
    """
    Fetches stars, forks, and last commit timestamp for a given GitHub repository.
    """
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner_repo)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Automated-Repo-Fetcher")
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        req.add_header("Authorization", f"token {github_token}")
        
    try:
        with urllib.request.urlopen(req) as response:
            item = json.loads(response.read().decode('utf-8'))
            pushed_at = item.get('pushed_at', '')
            if pushed_at and 'T' in pushed_at:
                last_commit = pushed_at.replace("T", " ").replace("Z", " UTC")
            else:
                last_commit = pushed_at
            return {
                "stars": item.get('stargazers_count', 0),
                "forks": item.get('forks_count', 0),
                "last_commit": last_commit
            }
    except Exception as e:
        logger.error(f"Error fetching repo stats for '{owner_repo}': {e}")
        return {}
