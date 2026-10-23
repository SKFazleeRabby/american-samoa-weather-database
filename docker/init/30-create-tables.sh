#!/bin/bash
# The PostgreSQL entrypoint sources this file after 20-extensions.sql.
python3 /opt/weather/create_tables.py
