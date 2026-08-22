# Automation

This repository runs a zero-dependency Python script via GitHub Actions to fetch the latest papers and repositories related to Image Dewarping.

The script fetches data using arXiv and GitHub APIs concurrently, uses a functional pipeline with Dependency Injection to process items via an LLM, and automatically opens a Pull Request with the updated `README.md`.

- The codebase is fully typed via Type Hints.
- Uses strict FP, Dependency Injection, and Python `logging`.
- No hardcoded parameters; all configurations and topics are dynamically loaded from `config.json`.