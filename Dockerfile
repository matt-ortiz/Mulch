FROM python:3.11-slim

WORKDIR /mulch

# Install system dependencies
RUN apt-get update && apt-get install -y \
    nginx \
    wkhtmltopdf \
    && rm -rf /var/lib/apt/lists/*

# Explicitly install gunicorn
RUN pip install gunicorn

# Install Python dependencies
RUN pip install flask flask-cors flask-bootstrap flask-caching flask-login \
    flask-migrate flask-sqlalchemy flask-wtf geopy googlemaps python-dotenv \
    requests opencage

# Copy nginx configuration ONLY
COPY nginx.conf /etc/nginx/nginx.conf

# DO NOT create any directories or copy any app files
# They should all come from the volume mount

# Ensure proper permissions
RUN chown -R www-data:www-data /mulch && \
    chmod -R 755 /mulch

# Add debug commands to startup
CMD echo "Listing /mulch contents:" && \
    ls -la /mulch && \
    echo "Listing /mulch/app/templates contents:" && \
    ls -la /mulch/app/templates && \
    service nginx start && \
    /usr/local/bin/gunicorn --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120 \
    --access-logfile /dev/stdout \
    --error-logfile /dev/stderr \
    run:app 