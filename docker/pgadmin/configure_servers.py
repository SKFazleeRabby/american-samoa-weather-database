"""Generate pgAdmin's connection from Compose environment, then start pgAdmin."""

import json
import os
from pathlib import Path


def server_config(environment):
    return {
        "Servers": {
            "1": {
                "Name": "Weather Database",
                "Group": "Weather",
                "Host": "db",
                "Port": 5432,
                "MaintenanceDB": environment["POSTGRES_DB"],
                "Username": environment["POSTGRES_USER"],
                "SSLMode": "prefer",
            }
        }
    }


if __name__ == "__main__":
    target = Path(os.environ["PGADMIN_SERVER_JSON_FILE"])
    target.write_text(json.dumps(server_config(os.environ)), encoding="utf-8")
    os.execv("/entrypoint.sh", ["/entrypoint.sh"])
