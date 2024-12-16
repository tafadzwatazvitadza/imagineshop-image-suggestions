#!/bin/bash
# start.sh

# Exit immediately if a command exits with a non-zero status
set -e

# Set Flask environment variables
export FLASK_APP=app.py
export FLASK_ENV=production  # or development as needed

# Run database migrations
flask db upgrade

# Start the Gunicorn server
exec gunicorn app:app --timeout 120 --workers=3
