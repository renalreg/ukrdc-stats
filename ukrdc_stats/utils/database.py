import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, Session
from ukrdc_stats.utils.cache import cachedsessionmaker


def load_ukrdc_url_from_config(server_name: str, keypath: str) -> URL:
    """Load the UKRDC configuration from a JSON file."""

    config_path = Path(keypath)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found at {config_path}")

    with open(config_path, "r") as f:
        ukrdc_config: dict[str, Any] = json.load(f)

    servers = ukrdc_config.get("servers")
    if isinstance(servers, dict):
        db_config = servers.get(server_name)
    else:
        db_config = ukrdc_config.get(server_name)

    if not isinstance(db_config, dict):
        raise ValueError(
            f"Server configuration for {server_name} not found in UKRDC config"
        )

    host = db_config.get("db_host")
    port = db_config.get("db_port")
    dbname = db_config.get("db_name")
    user = db_config.get("db_user")
    password = db_config.get("db_password")

    return URL.create(
        drivername="postgresql+psycopg",
        username=str(user),
        password=str(password),
        host=str(host),
        port=int(port),
        database=str(dbname),
    )


def get_sessionmaker(
    server_name: str, keypath: str, caching: bool = False
) -> sessionmaker:
    """Create a SQL session for the specified server using the UKRDC configuration.

    Args:
        server_name (str): The name of the server to connect to.
        keypath (str): Path to the UKRDC configuration file.

    Returns:
        sessionmaker: A SQLAlchemy sessionmaker for database operations.
    """

    db_url = load_ukrdc_url_from_config(server_name, keypath)
    engine = create_engine(db_url)

    if caching:
        return cachedsessionmaker(bind=engine)
    else:
        return sessionmaker(bind=engine)


def get_archive_sessionmaker(session: Session) -> sessionmaker:
    """helper function to take a ukrdc session and generate an xml archive
    session on the same database cluster.

    Args:
        session (Session): session for ukrdc database

    Returns:
        Session: session for ukrdc xml archive database
    """
    db_url = session.bind.url

    password = db_url.password
    username = db_url.username
    host = db_url.host
    port = db_url.port
    drivername = db_url.drivername
    database = "removed_xml_archive"

    new_url = f"{drivername}://{username}:{password}@{host}:{port}/{database}"
    engine = create_engine(new_url)

    return sessionmaker(bind=engine)
