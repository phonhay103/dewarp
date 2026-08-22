from typing import TypedDict, List, Optional
from datetime import datetime

class Entry(TypedDict, total=False):
    id: str
    title: str
    paper_url: str
    code_url: str
    date: str
    year: str
    authors: str
    description: Optional[str]
    category: Optional[str]
    summary: Optional[str]
    stars: Optional[int]
    forks: Optional[int]
    last_commit: Optional[str]

class TopicQuery(TypedDict):
    arxiv: str
    github: str

class Topic(TypedDict):
    name: str
    queries: TopicQuery
    categories: List[str]

class LlmConfig(TypedDict):
    model: str
    system_prompt: str
    user_prompt_template: str
class Settings(TypedDict, total=False):
    fetch_days: int

class AppConfig(TypedDict):
    settings: Settings
    topics: List[Topic]
    llm: LlmConfig

class Database(TypedDict):
    entries: List[Entry]
