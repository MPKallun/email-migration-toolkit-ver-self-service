#!/bin/bash
cd "$(dirname "$0")"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed. Please download it from https://www.python.org/downloads/"
    read -p "Press enter to exit..."
    exit 1
fi

# Set up virtual environment
if [ ! -d "venv" ]; then
    echo "First-time setup: Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install dependencies
source venv/bin/activate
echo "Updating packages..."
pip install --upgrade pip > /dev/null 2>&1
pip install customtkinter

# Run the app
echo "Launching Data Backup app..."
python3 src/gui.py
