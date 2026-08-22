import os
import json
import urllib.parse
import urllib.request
import logging
import subprocess
import shutil
from datetime import datetime
from typing import List, Dict, Optional
from models import Entry
logger = logging.getLogger(__name__)

def get_github_token() -> Optional[str]:
    """
    Resolves the GitHub token from environment variable 'GITHUB_TOKEN' or local 'gh' CLI.
    Sets os.environ["GITHUB_TOKEN"] if resolved from gh CLI for current process.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
        
    if shutil.which("gh"):
        try:
            res = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
                os.environ["GITHUB_TOKEN"] = token
                logger.info("Using GitHub authentication token from local 'gh' CLI.")
                return token
        except Exception as e:
            logger.debug(f"Failed to fetch token from gh CLI: {e}")
            
    return None

def fetch_github(query: str, start_date: datetime, min_stars: int = 0) -> List[Entry]:
    """
    Fetches repositories from the GitHub API based on a query and a start date.
    
    Args:
        query (str): The search query for GitHub.
        start_date (datetime): The cutoff date for repositories to include.
        min_stars (int): The minimum star count required for repositories.
        
    Returns:
        List[Entry]: A list of repository metadata dictionaries.
    """
    date_str = start_date.strftime('%Y-%m-%d')
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}+created:>={date_str}&sort=stars&order=desc"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Automated-Repo-Fetcher")
    
    github_token = get_github_token()
    if github_token:
        req.add_header("Authorization", f"token {github_token}")
        
    results = []
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            for item in data.get('items', []):
                stars = item.get('stargazers_count', 0)
                if stars < min_stars:
                    logger.info(f"Ignoring GitHub repo '{item['full_name']}' because star count ({stars}) is below minimum required ({min_stars}).")
                    continue

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
                    "stars": stars,
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
    github_token = get_github_token()
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


def fetch_batch_repo_stats(owner_repos: List[str]) -> Dict[str, dict]:
    """
    Fetches stars, forks, and last commit timestamp for multiple GitHub repositories
    in a SINGLE GraphQL API request.
    """
    if not owner_repos:
        return {}
        
    github_token = get_github_token()
    if not github_token:
        logger.warning("GITHUB_TOKEN and local 'gh' auth are not available. Skipping batch GitHub stats update.")
        return {}

    results: Dict[str, dict] = {}
    batch_size = 100
    
    for i in range(0, len(owner_repos), batch_size):
        chunk = owner_repos[i:i + batch_size]
        valid_chunk = []
        subqueries = []
        
        for idx, owner_repo in enumerate(chunk):
            parts = owner_repo.strip('/').split('/')
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
                alias = f"repo_{idx}"
                safe_owner = json.dumps(owner)
                safe_repo = json.dumps(repo)
                subqueries.append(
                    f"{alias}: repository(owner: {safe_owner}, name: {safe_repo}) {{ "
                    f"stargazerCount forkCount pushedAt }}"
                )
                valid_chunk.append((alias, owner_repo))
                
        if not subqueries:
            continue
            
        query = f"query {{ {' '.join(subqueries)} }}"
        url = "https://api.github.com/graphql"
        req = urllib.request.Request(url, data=json.dumps({"query": query}).encode('utf-8'))
        req.add_header("User-Agent", "Automated-Repo-Fetcher")
        req.add_header("Authorization", f"Bearer {github_token}")
        req.add_header("Content-Type", "application/json")
        
        try:
            with urllib.request.urlopen(req) as response:
                resp_data = json.loads(response.read().decode('utf-8'))
                data = resp_data.get('data', {}) or {}
                for alias, owner_repo in valid_chunk:
                    repo_info = data.get(alias)
                    if repo_info and isinstance(repo_info, dict):
                        pushed_at = repo_info.get('pushedAt', '')
                        if pushed_at and 'T' in pushed_at:
                            last_commit = pushed_at.replace("T", " ").replace("Z", " UTC")
                        else:
                            last_commit = pushed_at
                        results[owner_repo] = {
                            "stars": repo_info.get('stargazerCount', 0),
                            "forks": repo_info.get('forkCount', 0),
                            "last_commit": last_commit
                        }
        except Exception as e:
            logger.error(f"Error fetching batch GraphQL repo stats: {e}")
            
    return results
