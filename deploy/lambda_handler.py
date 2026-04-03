"""AWS Lambda entrypoint.

Wraps the FastAPI app with Mangum for Lambda + API Gateway deployment.
"""

from mangum import Mangum  # type: ignore

from src.server.webhook import app

handler = Mangum(app, lifespan="auto")
