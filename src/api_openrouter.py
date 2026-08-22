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
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Uses an LLM via OpenRouter to analyze an item based on a provided schema.
    
    Args:
        item (Entry): The item metadata.
        config (AppConfig): The overall configuration.
        topic (Topic): The topic configuration.
        schema (Dict[str, Any]): The JSON schema to enforce.
        schema_name (str): The name for the JSON schema wrapper.
        
    Returns:
        Tuple[Dict[str, Any], Dict[str, Any]]: (parsed JSON response, token usage info)
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
    
    llm_cfg = config.get('llm', {})
    models_to_try = []
    
    if 'models' in llm_cfg and isinstance(llm_cfg['models'], list):
        models_to_try.extend(llm_cfg['models'])
    if 'model' in llm_cfg and llm_cfg['model'] not in models_to_try:
        models_to_try.append(llm_cfg['model'])
    if 'fallback_models' in llm_cfg and isinstance(llm_cfg['fallback_models'], list):
        for m in llm_cfg['fallback_models']:
            if m not in models_to_try:
                models_to_try.append(m)
                
    if not models_to_try:
        models_to_try = ["nvidia/nemotron-3-nano-30b-a3b:free"]

    response_format = build_response_format(name=schema_name, schema=schema)
    
    default_usage = {
        "model": "None",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0
    }

    for model in models_to_try:
        data = {
            "model": model,
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
                parsed = json.loads(content)
                
                usage_resp = resp_data.get('usage', {})
                prompt_tokens = usage_resp.get('prompt_tokens') or usage_resp.get('input_tokens') or 0
                completion_tokens = usage_resp.get('completion_tokens') or usage_resp.get('output_tokens') or 0
                
                cached_tokens = 0
                prompt_details = usage_resp.get('prompt_tokens_details')
                if isinstance(prompt_details, dict):
                    cached_tokens = prompt_details.get('cached_tokens') or 0
                elif usage_resp.get('cache_read_input_tokens') is not None:
                    cached_tokens = usage_resp.get('cache_read_input_tokens') or 0

                total_tokens = usage_resp.get('total_tokens') or (prompt_tokens + completion_tokens)

                usage_info = {
                    "model": resp_data.get('model', model),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cached_tokens": cached_tokens,
                    "total_tokens": total_tokens
                }

                if parsed:
                    return parsed, usage_info
        except Exception as e:
            logger.warning(f"Model '{model}' failed for item '{item['title']}': {e}. Trying fallback if available...")

    logger.error(f"All models failed for item '{item['title']}'.")
    return {}, default_usage