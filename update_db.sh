#!/bin/bash
export FLASK_APP=run.py
.venv/bin/flask db migrate -m "increase password hash length"
.venv/bin/flask db upgrade 