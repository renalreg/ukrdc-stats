"""Database session creation from UKRDC config files."""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from ukrdc_stats.utils.cache import cachedsessionmaker


def load_ukrdc_url_from_config(server_name: str, keypath: str) -> URL:
    """Load the UKRDC database URL from a JSON config file.

    Args:
        server_name (str): The name of the server to connect to.
        keypath (str): Path to the UKRDC configuration file.

    Returns:
        URL: SQLAlchemy URL for the configured server.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the server is not present in the config.
    """
    config_path = Path(keypath)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found at {config_path}")

    with config_path.open() as f:
        ukrdc_config: dict[str, Any] = json.load(f)

    servers = ukrdc_config.get("servers")
    if isinstance(servers, dict):
        db_config = servers.get(server_name)
    else:
        db_config = ukrdc_config.get(server_name)

    if not isinstance(db_config, dict):
        # missing config entry, not a type error on a call argument
        raise ValueError(  # noqa: TRY004
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
        port=int(port) if port is not None else None,
        database=str(dbname),
    )


def get_sessionmaker(
    server_name: str, keypath: str, caching: bool = False
) -> sessionmaker[Session]:
    """Create a SQL session for the specified server using the UKRDC configuration.

    Args:
        server_name (str): The name of the server to connect to.
        keypath (str): Path to the UKRDC configuration file.
        caching (bool): Wrap the sessionmaker with the dogpile query cache.
            Defaults to False.

    Returns:
        sessionmaker[Session]: A SQLAlchemy sessionmaker for database operations.
    """
    db_url = load_ukrdc_url_from_config(server_name, keypath)
    engine = create_engine(db_url)

    if caching:
        return cachedsessionmaker(bind=engine)
    return sessionmaker(bind=engine)


def get_archive_sessionmaker(session: Session) -> sessionmaker[Session]:
    """Generate an xml archive sessionmaker on the same cluster as a session.

    Args:
        session (Session): session for ukrdc database

    Returns:
        sessionmaker[Session]: sessionmaker for the ukrdc xml archive database
    """
    # get_bind() is typed loosely by SQLAlchemy (Engine | Connection); narrow
    # it so the URL access is type-safe in both branches
    bind = session.get_bind()
    db_url = bind.url if isinstance(bind, Engine) else bind.engine.url

    new_url = URL.create(
        drivername=db_url.drivername,
        username=db_url.username,
        password=db_url.password,
        host=db_url.host,
        port=db_url.port,
        database="removed_xml_archive",
    )
    engine = create_engine(new_url)

    return sessionmaker(bind=engine)
