"""Flask + MongoDB demo service.

Configuration is 12-factor style (environment variables only):

  MONGODB_URI        Full URI. If set, it wins over everything below (Part 1 compatibility).
  MONGO_HOST         default "localhost"   (in Kubernetes: the Service name, "mongodb")
  MONGO_PORT         default "27017"
  MONGO_DB           default "flask_db"
  MONGO_USERNAME     application user (least privilege: readWrite on MONGO_DB only)
  MONGO_PASSWORD     application password
  MONGO_AUTH_SOURCE  default = MONGO_DB (the DB where the user was created)
"""

import logging
import os
import time
from datetime import datetime
from urllib.parse import quote_plus

from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

log = logging.getLogger("flask-app")

app = Flask(__name__)


def build_mongo_uri() -> str:
    """Build the MongoDB connection string."""
    explicit = os.environ.get("MONGODB_URI")

    if explicit:
        return explicit

    host = os.environ.get("MONGO_HOST", "localhost")
    port = os.environ.get("MONGO_PORT", "27017")
    db_name = os.environ.get("MONGO_DB", "flask_db")
    user = os.environ.get("MONGO_USERNAME")
    password = os.environ.get("MONGO_PASSWORD")
    auth_source = os.environ.get("MONGO_AUTH_SOURCE", db_name)

    if user and password:
        return (
            f"mongodb://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{db_name}?authSource={auth_source}"
        )

    return f"mongodb://{host}:{port}/"


# MongoClient is lazy.
client = MongoClient(
    build_mongo_uri(),
    serverSelectionTimeoutMS=3000
)

db = client[os.environ.get("MONGO_DB", "flask_db")]
collection = db.data


@app.route("/")
def index():
    return f"Welcome to the Flask app! The current time is: {datetime.now()}"


@app.route("/data", methods=["GET", "POST"])
def data():
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True)

            if not isinstance(payload, dict) or not payload:
                return jsonify({
                    "error": "Body must be a non-empty JSON object"
                }), 400

            collection.insert_one(payload)

            return jsonify({
                "status": "Data inserted"
            }), 201

        docs = list(collection.find({}, {"_id": 0}))

        return jsonify(docs), 200

    except PyMongoError:
        log.exception("MongoDB operation failed")

        return jsonify({
            "error": "Database unavailable"
        }), 503


@app.route("/healthz")
def healthz():
    """Liveness probe."""
    return jsonify({
        "status": "ok"
    }), 200


@app.route("/readyz")
def readyz():
    """Readiness probe."""
    try:
        db.command("ping")

        return jsonify({
            "status": "ready"
        }), 200

    except PyMongoError:
        return jsonify({
            "status": "mongodb unreachable"
        }), 503


@app.route("/load")
def load():
    """Temporary CPU-load endpoint for demonstrating Kubernetes HPA."""

    end_time = time.time() + 10

    while time.time() < end_time:
        total = 0

        for i in range(10000):
            total += i * i

    return jsonify({
        "status": "load complete"
    }), 200


if __name__ == "__main__":
    # Local development only.
    # In Kubernetes the container runs gunicorn.
    app.run(
        host="0.0.0.0",
        port=5000
    )