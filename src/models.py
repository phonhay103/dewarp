from typing import TypedDict


class Entry(TypedDict, total=False):
    id: str
    title: str
    paper_url: str
    code_url: str
    date: str
    year: str
    authors: str
    description: str | None
    tags: list[str] | None
    summary: str | None
    stars: int | None
    forks: int | None
    last_commit: str | None

class TopicQuery(TypedDict):
    arxiv: str
    github: str

class Topic(TypedDict):
    name: str
    queries: TopicQuery
    tags: list[str]

class LlmConfig(TypedDict, total=False):
    model: str
    fallback_models: list[str]
    models: list[str]
    system_prompt: str
    user_prompt_template: str
class Settings(TypedDict, total=False):
    fetch_days: int
    min_stars: int

class AppConfig(TypedDict):
    settings: Settings
    topics: list[Topic]
    llm: LlmConfig

class Database(TypedDict):
    entries: list[Entry]
