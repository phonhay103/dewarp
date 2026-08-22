import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from typing import List
from models import Entry
logger = logging.getLogger(__name__)

def fetch_arxiv(query: str, start_date: datetime) -> List[Entry]:
    """
    Fetches papers from the arXiv API based on a query and a start date.
    
    Args:
        query (str): The search query for arXiv.
        start_date (datetime): The cutoff date for papers to include.
        
    Returns:
        List[Entry]: A list of paper metadata dictionaries.
    """
    url = f"http://export.arxiv.org/api/query?search_query={urllib.parse.quote(query)}&sortBy=submittedDate&sortOrder=descending"
    req = urllib.request.Request(url)
    results = []
    
    try:
        with urllib.request.urlopen(req) as response:
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        for entry in root.findall('atom:entry', ns):
            published_str = entry.find('atom:published', ns).text
            pub_date = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
            
            if pub_date >= start_date:
                id_val = entry.find('atom:id', ns).text
                title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
                summary_node = entry.find('atom:summary', ns)
                description = summary_node.text.replace('\n', ' ').strip() if summary_node is not None else ""
                
                authors = ", ".join([
                    a.find('atom:name', ns).text 
                    for a in entry.findall('atom:author', ns)
                ])
                
                results.append({
                    "id": id_val,
                    "title": title,
                    "paper_url": id_val,
                    "code_url": "",
                    "date": pub_date.strftime('%Y-%m-%d'),
                    "year": pub_date.strftime('%Y'),
                    "authors": authors,
                    "description": description
                })
    except Exception as e:
        logger.error(f"Error fetching arXiv data: {e}")
        
    return results
