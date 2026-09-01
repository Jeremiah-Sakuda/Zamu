# The coordinator console.
#
# NEXT_PUBLIC_* values are inlined at build time, so the API URL is a build argument
# rather than a runtime environment variable.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
ARG NEXT_PUBLIC_ZAMU_API=http://localhost:8000
ARG NEXT_PUBLIC_ZAMU_ORG=org_demo_riverside
ENV NEXT_PUBLIC_ZAMU_API=$NEXT_PUBLIC_ZAMU_API \
    NEXT_PUBLIC_ZAMU_ORG=$NEXT_PUBLIC_ZAMU_ORG \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
