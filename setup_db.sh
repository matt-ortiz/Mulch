#!/bin/bash
rm -rf migrations
export FLASK_APP=run.py
.venv/bin/flask db init
.venv/bin/flask db migrate -m "migrate to postgresql"
.venv/bin/flask db upgrade 