from db.config import load_config


def get_db_url() -> str:
    """Return the configured database URL from the project's config loader."""
    cfg = load_config()
    return cfg['db_url']


def build_report():
    cfg = load_config()
    return f"report for {cfg['db_url']}"
