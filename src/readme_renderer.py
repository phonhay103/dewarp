from typing import Dict, Any

def render_readme(config: Dict[str, Any], db: Dict[str, Any]) -> str:
    """
    Renders the data into a Markdown formatted string categorized by tags.
    
    Args:
        config (Dict[str, Any]): The overall configuration.
        db (Dict[str, Any]): The database containing papers and repos.
        
    Returns:
        str: The generated Markdown content for README.md.
    """
    topic = config["topics"][0]
    tags = topic.get("tags", topic.get("categories", ["Uncategorized"]))
    
    md = f"# {topic['name']} Papers & Repos\n\n"
    md += f"This repository is automatically updated monthly with the latest papers and repositories related to **{topic['name']}**.\n\n"

    entries = db.get("entries", [])
    
    # Normalize tags on all entries for backwards compatibility
    for entry in entries:
        if "tags" not in entry or not entry["tags"]:
            if "category" in entry and entry["category"]:
                entry["tags"] = [entry["category"]]
            else:
                entry["tags"] = ["Uncategorized"]

    for tag in tags:
        # Find all items that contain this tag
        items = [e for e in entries if tag in e.get("tags", [])]
        if not items:
            continue
        
        md += f"## {tag}\n"
        
        # Sort items: date/year descending, stars descending
        def sort_key(item: Dict[str, Any]) -> tuple:
            date_str = str(item.get('date') or item.get('year') or '0000')
            stars = item.get('stars') if item.get('stars') is not None else -1
            return (date_str, stars)

        items.sort(key=sort_key, reverse=True)
        
        for item in items:
            title = item['title']
            year = item.get('year', '')
            year_str = f" ({year})" if year else ""
            
            md += f"- **{title}**{year_str}\n"
            if item.get('paper_url'):
                md += f"  - Paper: {item['paper_url']}\n"
            if item.get('code_url'):
                md += f"  - Code: {item['code_url']}\n"
            
            item_tags = item.get('tags', [])
            if item_tags:
                md += f"  - Tags: {', '.join(item_tags)}\n"

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
