"""The HTTP service, on Lambda.

The same FastAPI application, wrapped for a Lambda Function URL. Nothing about Zamu
changes here — this file exists so the coordinator API and the volunteer's one-tap
pages can run on a Lambda with a Function URL rather than needing a container and a
load balancer, which is the difference between a few dollars a month and a few
hundred for an organization that currently pays nothing for anything.

Deployed with `ZAMU_STORE=dynamodb`, so the container is stateless and cold starts
cost nothing but latency.
"""

from __future__ import annotations

from mangum import Mangum

from zamu.api.app import app

#: Lambda entrypoint. `lifespan="off"` because the app has no startup work to do; the
#: store is built lazily on first use and reused across invocations on a warm sandbox.
handler = Mangum(app, lifespan="off")
