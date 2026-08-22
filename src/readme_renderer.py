from typing import Dict, Any

def render_readme(config: Dict[str, Any], db: Dict[str, Any]) -> str:
    """
    Renders the data into a Markdown formatted string.
    
    Args:
        config (Dict[str, Any]): The overall configuration.
        db (Dict[str, Any]): The database containing papers and repos.
        
    Returns:
        str: The generated Markdown content for README.md.
    """
    topic = config["topics"][0]
    md = f"# {topic['name']} Papers & Repos\n\n"
    md += f"This repository is automatically updated weekly with the latest papers and repositories related to **{topic['name']}**.\n\n"

    for category in topic["categories"]:
        items = [e for e in db.get("entries", []) if e.get("category") == category]
        if not items:
            continue
        
        md += f"## {category}\n"
        
        # Sort items by date descending if possible, or just year
        items.sort(key=lambda x: x.get('date', '0000'), reverse=True)
        
        for item in items:
            title = item['title']
            year = item.get('year', '')
            year_str = f" ({year})" if year else ""
            
            md += f"- **{title}**{year_str}\n"
            if item.get('paper_url'):
                md += f"  - Paper: {item['paper_url']}\n"
            if item.get('code_url'):
                md += f"  - Code: {item['code_url']}\n"
            
            stats = []
            if item.get('stars') is not None:
                stats.append(f"Stars: {item['stars']}")
            if item.get('forks') is not None:
                stats.append(f"Forks: {item['forks']}")
            if item.get('last_commit'):
                stats.append(f"Last Commit: {item['last_commit']}")
            if stats:
                md += f"  - {' | '.join(stats)}\n"
            if item.get('summary'):
                md += f"  - {item['summary']}\n"
            md += "\n"

    md += "---\n*Updated automatically by GitHub Actions.*\n"
    return md
