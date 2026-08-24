FROM node:22-slim AS dashboard
WORKDIR /build
COPY dashboard/package.json dashboard/vite.config.js dashboard/index.html ./
COPY dashboard/src ./src
RUN npm install && npm run build

FROM python:3.12-slim
WORKDIR /app
COPY core/pyproject.toml core/README.md ./core/
COPY core/src ./core/src
COPY server/pyproject.toml ./server/
COPY server/src ./server/src
RUN pip install --no-cache-dir ./core ./server
COPY --from=dashboard /build/dist /app/static
ENV MG_DASHBOARD_DIST=/app/static
EXPOSE 8787
CMD ["uvicorn", "metergraph_server.main:app", "--host", "0.0.0.0", "--port", "8787"]
