#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Activate virtual environment
source venv/bin/activate

# Export environment variables
export FLASK_APP=app.py
export FLASK_ENV=development

# Apply database migrations
flask db upgrade

# Start the Flask server
flask run

