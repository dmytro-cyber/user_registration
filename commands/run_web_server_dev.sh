#!/bin/sh

# Run web server
uvicorn main:app --host 0.0.0.0 --port 8000 --ssl-keyfile /usr/src/certs/key.pem --ssl-certfile /usr/src/certs/cert.pem --reload --reload-dir /usr/src/fastapi
