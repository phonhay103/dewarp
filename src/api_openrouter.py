import os
import json
import urllib.request
import logging
from typing import Tuple, Dict, Any
from models import Entry, AppConfig, Topic
logger = logging.getLogger(__name__)

def build_response_format(name: str, schema: dict) -> dict:
    """
    Dynamically builds the JSON Schema agnostic wrapper required for OpenRouter's Structured Outputs.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema
        }
    }

def analyze_with_llm(
    item: Entry, 
    config: AppConfig, 
    topic: Topic,
    schema: Dict[str, Any],
    schema_name: str = "StructuredResponse"
) -> Dict[str, Any]:
    """
    Uses an LLM via OpenRouter to analyze an item based on a provided schema.
    
    Args:
        item (Entry): The item metadata.
        config (AppConfig): The overall configuration.
        topic (Topic): The topic configuration.
        schema (Dict[str, Any]): The JSON schema to enforce.
        schema_name (str): The name for the JSON schema wrapper.
        
    Returns:
        Dict[str, Any]: The parsed JSON response matching the schema.
    """
    system_prompt = config['llm']['system_prompt']
    description = item.get('description') or ''
    if not description and item.get('summary') and not item.get('summary', '').startswith('Error'):
        description = item['summary']
    if not description:
        description = item['title']

    user_prompt = config['llm']['user_prompt_template'].format(
        topic_name=topic['name'],
        title=item['title'],
        description=description
    )
    url = "https://openrouter.ai/api/v1/chat/completions"
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    response_format = build_response_format(name=schema_name, schema=schema)
    
    data = {
        "model": config['llm']['model'],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": response_format
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8'), 
        headers=headers
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            content = resp_data['choices'][0]['message']['content']
            return json.loads(content)
            
    except Exception as e:
        logger.error(f"Error analyzing item '{item['title']}': {e}")
        return {}