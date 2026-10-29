"""Automatic entry point. Processes every CSV URL; no recursive crawling."""
from run_pipeline import run

if __name__ == "__main__":
    run("auto")
