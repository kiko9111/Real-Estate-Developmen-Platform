FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"
ENV APP_ENV=production DATABASE_URL=sqlite:////data/platform.db
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
