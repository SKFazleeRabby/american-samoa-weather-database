#!/bin/bash
# Runs after extensions and schema creation, only for an empty database volume.
/opt/weather/venv/bin/python /opt/weather/seed_history.py
